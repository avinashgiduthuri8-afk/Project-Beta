"""
PROJECT-ALPHA Persistent Storage Engine
"""

import json
import logging
import os
import shutil
import threading
import time
from datetime import datetime, timezone

from .config import *

_logger = logging.getLogger("vgx.storage")

# Single lock guarding the one TradingBotCrypto.json storage file.
_storage_lock = threading.Lock()


class VGXStorageError(RuntimeError):
    """Raised when VGX storage is in an unrecoverable state.

    Two scenarios trigger this:
      1. get_open_positions(): storage file exists but is unreadable/corrupt —
         callers in the risk engine deny trades fail-closed.
      2. load_data(): primary file is corrupt AND the .bak file is also
         corrupt or absent — deny-safe mode, no automatic overwrite.

    Not raised for a missing/empty file (fresh start is not an error).
    """

# Default coin list — used when grid_coins key is absent from storage.
_DEFAULT_GRID_COINS: list = ["BTC", "ETH", "SOL", "BNB", "XRP", "ZEC"]

# ============================================================
# RUNTIME VARIABLES
# ============================================================

virtual_balance = 1000000

positions = {}

trade_log = []

price_history = {}

market_cache = {}

portfolio_history = []

trade_history = []

error_logs = []

metrics_summary = {}

# ── Grid management globals ───────────────────────────────────────────────────
# grid_config: per-coin manual base-price overrides.
#   schema: {"BTC": {"base_price": float, "base_price_set_at": str, "base_price_set_by": str}}
# grid_coins: ordered list of active coins for the VGX grid.
grid_config: dict = {}
grid_coins: list = list(_DEFAULT_GRID_COINS)

# ============================================================
# STORAGE STATUS
# ============================================================

storage_state = {

    "status": "INITIALIZED",

    "last_sync": 0,

    "sync_count": 0,

    # NONE | RESTORED | FAILED
    "backup_status": "NONE",

    # True when the last load_data() call recovered state from the .bak file.
    # Dashboard can surface a warning banner when this is True.
    "recovered_from_backup": False,

}


# ============================================================
# VERIFY FILE
# ============================================================

def _verify_file(path):

    if not os.path.exists(path):
        return False

    if os.path.getsize(path) == 0:
        return False

    try:

        with open(path, "r", encoding="utf-8") as f:

            json.load(f)

        return True

    except Exception:

        return False


# ============================================================
# NORMALIZE STORAGE
# ============================================================

def _normalise(data):

    defaults = {

        "virtual_balance": 1000000,

        "positions": {},

        "trade_log": [],

        "price_history": {},

        "market_cache": {},

        "portfolio_history": [],

        "trade_history": [],

        "error_logs": [],

        "metrics_summary": {},

        # Grid management — safe defaults so old storage files upgrade silently.
        "grid_config": {},
        "grid_coins":  list(_DEFAULT_GRID_COINS),

    }

    for k, v in defaults.items():

        data.setdefault(k, v)

    # Type-coerce grid management fields so corrupt/unexpected storage values
    # degrade to safe defaults rather than propagating into callers.
    if not isinstance(data["grid_config"], dict):
        _logger.warning(
            "[VGX] grid_config has unexpected type %s — resetting to {}",
            type(data["grid_config"]).__name__,
        )
        data["grid_config"] = {}
    else:
        # Purge any entries that are not dicts (corrupted sub-records).
        data["grid_config"] = {
            k: v for k, v in data["grid_config"].items()
            if isinstance(v, dict)
        }

    if not isinstance(data["grid_coins"], list):
        _logger.warning(
            "[VGX] grid_coins has unexpected type %s — resetting to default list",
            type(data["grid_coins"]).__name__,
        )
        data["grid_coins"] = list(_DEFAULT_GRID_COINS)
    else:
        # Keep only string entries; drop anything else silently.
        data["grid_coins"] = [c for c in data["grid_coins"] if isinstance(c, str)]
        if not data["grid_coins"]:
            data["grid_coins"] = list(_DEFAULT_GRID_COINS)

    return data


# ============================================================
# INTERNAL HELPERS
# ============================================================

def _assign_globals(data: dict) -> None:
    """Write a normalised data dict into the module-level globals.

    Must be called while *_storage_lock* is held (or before the module is
    shared between threads, e.g. during load_data startup).
    """
    global virtual_balance, positions, trade_log, price_history
    global market_cache, portfolio_history, trade_history, error_logs
    global metrics_summary, grid_config, grid_coins

    virtual_balance   = data["virtual_balance"]
    positions         = data["positions"]
    trade_log         = data["trade_log"]
    price_history     = data["price_history"]
    market_cache      = data["market_cache"]
    portfolio_history = data["portfolio_history"]
    trade_history     = data["trade_history"]
    error_logs        = data["error_logs"]
    metrics_summary   = data["metrics_summary"]
    grid_config       = data["grid_config"]
    grid_coins        = data["grid_coins"]


def _load_json_file(path: str) -> dict:
    """Open *path* and return the parsed JSON dict.

    Raises ``json.JSONDecodeError`` or ``OSError`` on any failure — callers
    decide what to do with those exceptions.
    """
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ============================================================
# LOAD STORAGE
# ============================================================

def load_data() -> None:
    """Load VGX state from disk into module-level globals.

    Three distinct cases are handled:

    Case 1 — Primary file missing or empty (fresh start):
        Globals keep their module-level defaults.
        ``save_data()`` is called once to create a clean storage file.
        No existing state can be lost because there is none.

    Case 2 — Primary file exists but is corrupt:
        Sub-case A — ``.bak`` file is valid:
            State is restored from ``.bak``.  A WARNING is logged.
            ``storage_state["recovered_from_backup"]`` is set to True so
            callers and the dashboard can surface a recovery notice.
            ``save_data()`` is NOT called automatically — the operator
            should acknowledge the situation before the next write cycle
            overwrites the backup.
        Sub-case B — ``.bak`` is also corrupt or absent:
            ``VGXStorageError`` is raised.  Neither file is touched.
            The operator must perform manual recovery.

    Case 3 — Primary file exists and is valid:
        State is loaded normally.  Globals are updated.  Returns silently.

    Raises:
        VGXStorageError: when the primary file is corrupt and the backup
            cannot provide a valid recovery (Case 2-B).
    """
    os.makedirs(STORAGE_DIR, exist_ok=True)

    with _storage_lock:

        main_exists = (
            os.path.exists(STORAGE_FILE)
            and os.path.getsize(STORAGE_FILE) > 0
        )

        if not main_exists:
            # ── Case 1: fresh start ───────────────────────────────────────
            # Nothing to recover; globals stay at their module defaults.
            # Fall through to the save_data() call below (outside the lock).
            storage_state["status"] = "INITIALIZING"
            storage_state["backup_status"] = "NONE"
            storage_state["recovered_from_backup"] = False
            _logger.info(
                "[VGX] load_data: no storage file found — "
                "initialising with defaults."
            )

        elif _verify_file(STORAGE_FILE):
            # ── Case 3: happy path — primary file valid ───────────────────
            data = _load_json_file(STORAGE_FILE)
            data = _normalise(data)
            _assign_globals(data)
            storage_state["status"] = "SYNCED"
            storage_state["backup_status"] = "NONE"
            storage_state["recovered_from_backup"] = False
            return

        else:
            # ── Case 2: primary file exists but is corrupt ────────────────
            _logger.error(
                "[VGX] load_data: %s is corrupt — "
                "attempting recovery from backup %s.",
                STORAGE_FILE,
                STORAGE_BACKUP,
            )
            storage_state["status"] = "CORRUPT"
            storage_state["recovered_from_backup"] = False

            if _verify_file(STORAGE_BACKUP):
                # Sub-case A: backup is valid — restore from it
                _logger.warning(
                    "[VGX] load_data: RECOVERED from backup %s. "
                    "Positions, balances and trade history have been "
                    "preserved.  Corrupt primary file has NOT been "
                    "overwritten — manual inspection recommended.",
                    STORAGE_BACKUP,
                )
                data = _load_json_file(STORAGE_BACKUP)
                data = _normalise(data)
                _assign_globals(data)
                storage_state["status"] = "RECOVERED_FROM_BACKUP"
                storage_state["backup_status"] = "RESTORED"
                storage_state["recovered_from_backup"] = True
                # Do NOT call save_data() here — the operator should
                # acknowledge the corruption before the next auto-save
                # cycle promotes the backup data into the primary file.
                return

            else:
                # Sub-case B: backup also unusable — deny-safe mode
                bak_exists = os.path.exists(STORAGE_BACKUP)
                detail = (
                    "backup file is also corrupt"
                    if bak_exists
                    else "no backup file exists"
                )
                _logger.error(
                    "[VGX] load_data: CRITICAL — primary storage is "
                    "corrupt and %s.  Entering deny-safe mode.  "
                    "VGX will NOT trade until storage is manually "
                    "recovered.  Do NOT delete either file:\n"
                    "  Primary : %s\n"
                    "  Backup  : %s",
                    detail,
                    STORAGE_FILE,
                    STORAGE_BACKUP,
                )
                storage_state["status"] = "CORRUPT_UNRECOVERABLE"
                storage_state["backup_status"] = "FAILED"
                storage_state["recovered_from_backup"] = False
                raise VGXStorageError(
                    f"VGX storage corrupt and {detail}. "
                    "Manual recovery required — automatic overwrite "
                    "refused to prevent data loss."
                )

    # ── Case 1 only reaches here ──────────────────────────────────────────
    # Lock has been released; safe to call save_data() which acquires it.
    save_data()


# ============================================================
# SAVE STORAGE
# ============================================================

def save_data():

    with _storage_lock:

        os.makedirs(STORAGE_DIR, exist_ok=True)

        payload = {

            "virtual_balance": virtual_balance,

            "positions": positions,

            "trade_log": trade_log,
            "price_history": price_history,

            "market_cache": market_cache,

            "portfolio_history": portfolio_history,

            "trade_history": trade_history,

            "error_logs": error_logs,

            "metrics_summary": metrics_summary,

            # Grid management — must be included so save_data() never clobbers them.
            "grid_config": grid_config,
            "grid_coins":  grid_coins,

        }

        temp_file = STORAGE_FILE + ".tmp"

        with open(temp_file, "w", encoding="utf-8") as f:

            json.dump(payload, f, indent=4)

        if os.path.exists(STORAGE_FILE):

            shutil.copy2(

                STORAGE_FILE,

                STORAGE_BACKUP

            )

        os.replace(temp_file, STORAGE_FILE)

        storage_state["status"] = "SYNCED"

        storage_state["last_sync"] = time.time()

        storage_state["sync_count"] += 1


def _positions_dict_to_list(positions_dict: dict) -> list[dict]:
    """Convert VGX keyed positions dict to risk-engine list[dict] format.

    Each returned dict exposes `amount` and `trade_amount` so
    _deployed_capital() can sum deployed capital without any key-miss.
    """
    result = []
    for key, p in positions_dict.items():
        if not isinstance(p, dict):
            continue
        entry = {
            "coin":            p.get("coin", key.split("_")[0] if "_" in key else key),
            "buy_price":       p.get("buy_price", 0),
            "qty":             p.get("qty", 0),
            "amount":          p.get("amount", 0),
            "trade_amount":    p.get("amount", 0),
            "trailing_active": p.get("trailing_active", False),
        }
        result.append(entry)
    return result


def get_open_positions() -> list[dict]:
    """Return current open VGX positions as list[dict] for risk-engine deployed-capital checks.

    Each dict carries at least one of the keys _deployed_capital() expects:
    ('total_invested', 'total_cost', 'amount', 'trade_amount').

    Strategy:
      1. Prefer the in-memory `positions` dict — authoritative when the VGX
         bot is running in the same process (post load_data()).
      2. Fall back to a direct file read when in-memory is empty, covering
         the window before load_data() has been called in this process.

    On any file-read failure a loud logger.error is emitted so a silent
    zero-capital report is never invisible to operators.  Returns [] on
    failure (fail-safe: do not crash, but the operator must investigate).
    """
    # ── 1. Prefer in-memory (authoritative live state) ──────────────────────
    with _storage_lock:
        mem_snapshot = dict(positions)   # shallow copy inside the lock

    if mem_snapshot:
        return _positions_dict_to_list(mem_snapshot)

    # ── 2. Fall back to file (bot not yet started / load_data not called) ───
    file_exists = os.path.exists(STORAGE_FILE) and os.path.getsize(STORAGE_FILE) > 0
    if not file_exists:
        # File absent or empty — fresh start, no open positions.
        return []

    try:
        with open(STORAGE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        # File EXISTS but is unreadable/corrupt — this is a genuine risk-control
        # failure.  Raise so the risk engine can deny trades fail-closed rather
        # than silently treating deployed capital as 0.
        _logger.error(
            "[VGX] get_open_positions: storage file unreadable — "
            "risk engine will deny VGX trades until storage is restored. "
            "Error: %s", exc,
        )
        raise VGXStorageError(
            f"VGX storage file unreadable: {exc}"
        ) from exc

    positions_dict = data.get("positions", {})
    if not isinstance(positions_dict, dict):
        _logger.error(
            "[VGX] get_open_positions: 'positions' key has unexpected type %s — "
            "risk engine will deny VGX trades until storage is restored.",
            type(positions_dict).__name__,
        )
        raise VGXStorageError(
            f"VGX storage 'positions' key has unexpected type {type(positions_dict).__name__}"
        )

    return _positions_dict_to_list(positions_dict)


# ============================================================
# GRID MANAGEMENT — PUBLIC API
# ============================================================
# All six functions are synchronous; call them from async handlers
# via asyncio.to_thread (mutating operations carry file I/O via save_data).


def get_grid_config() -> dict:
    """Return the current grid_config dict (in-memory, always in sync with file).

    Returns {} when no manual base prices have been set.
    Synchronous — wrap in asyncio.to_thread when calling from an async handler.
    """
    with _storage_lock:
        return dict(grid_config)


def get_coin_base_price(coin: str) -> float | None:
    """Return the manual base price for *coin* if one is set, else None.

    None means the caller should fall back to the live market price.
    """
    with _storage_lock:
        entry = grid_config.get(coin)
    if entry and isinstance(entry, dict):
        price = entry.get("base_price")
        if price is not None and float(price) > 0:
            return float(price)
    return None


def set_coin_base_price(coin: str, price: float, set_by: str = "dashboard") -> bool:
    """Set a manual grid-centre base price for *coin*.

    Validates price > 0, writes to grid_config[coin] with ISO timestamp,
    then persists via save_data().

    Returns True on success, False on validation failure or write error.
    """
    global grid_config

    if not isinstance(price, (int, float)) or price <= 0:
        _logger.warning(
            "[VGX] set_coin_base_price rejected: coin=%s price=%r (must be > 0)",
            coin, price,
        )
        return False

    now_iso = datetime.now(timezone.utc).isoformat()

    with _storage_lock:
        # Build a fresh dict so we don't mutate a reference held by callers.
        grid_config = dict(grid_config)
        grid_config[coin] = {
            "base_price":        float(price),
            "base_price_set_at": now_iso,
            "base_price_set_by": str(set_by),
        }

    _logger.info(
        "[VGX] Base price set: coin=%s price=%s set_by=%s",
        coin, price, set_by,
    )

    save_data()
    return True


def remove_coin_base_price(coin: str) -> bool:
    """Remove the manual base price override for *coin*.

    Returns True if an entry was found and removed, False if coin not present.
    """
    global grid_config

    with _storage_lock:
        if coin not in grid_config:
            return False
        grid_config = dict(grid_config)
        grid_config.pop(coin, None)

    _logger.info("[VGX] Base price removed: coin=%s", coin)
    save_data()
    return True


def get_grid_coins() -> list:
    """Return the ordered list of active VGX grid coins.

    Falls back to _DEFAULT_GRID_COINS when the storage key is absent or empty.
    """
    with _storage_lock:
        coins = list(grid_coins)
    return coins if coins else list(_DEFAULT_GRID_COINS)


def set_grid_coins(coins: list) -> bool:
    """Replace the active VGX grid coin list.

    Validates:
    - List must not be empty.
    - Each coin must be alphanumeric (no special characters).
    - Maximum 20 coins.

    Returns True on success, False on validation failure or write error.
    """
    global grid_coins

    if not coins:
        _logger.warning("[VGX] set_grid_coins rejected: empty list")
        return False

    if len(coins) > 20:
        _logger.warning(
            "[VGX] set_grid_coins rejected: %d coins exceeds maximum of 20",
            len(coins),
        )
        return False

    for c in coins:
        if not isinstance(c, str) or not c.isalnum():
            _logger.warning(
                "[VGX] set_grid_coins rejected: %r is not alphanumeric", c
            )
            return False
        if len(c) > 10:
            _logger.warning(
                "[VGX] set_grid_coins rejected: %r exceeds 10-character limit", c
            )
            return False

    # Normalise to uppercase and deduplicate (preserve first occurrence order).
    seen: set = set()
    normalised: list = []
    for c in coins:
        up = c.upper()
        if up not in seen:
            seen.add(up)
            normalised.append(up)

    with _storage_lock:
        grid_coins = normalised

    _logger.info("[VGX] Grid coins updated: %s", normalised)
    save_data()
    return True
