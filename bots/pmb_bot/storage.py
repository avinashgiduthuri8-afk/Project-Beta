"""
PROJECT-ALPHA PMB Bot local JSON storage.

Files: data/watchlist.json, data/positions.json, data/trades.json, data/stats.json.
All writes are atomic (tmp→replace) with .bak backups.
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("pmb_bot.storage")

# One lock per state file — used in every save_* function body.
_positions_lock = threading.Lock()
_trades_lock    = threading.Lock()
_stats_lock     = threading.Lock()

from .config import (
    DATA_DIR,
    INITIAL_CASH_BALANCE,
    BASE_BUY,
    POSITIONS_FILE,
    STATS_FILE,
    TRADES_FILE,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists() or path.stat().st_size == 0:
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        backup = path.with_suffix(path.suffix + ".bak")
        if backup.exists():
            try:
                return json.loads(backup.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        return default


def _write_json(path: Path, data: Any) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
        except OSError:
            pass
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def ensure_storage() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not POSITIONS_FILE.exists():
        _write_json(POSITIONS_FILE, {"positions": []})
    if not TRADES_FILE.exists():
        _write_json(TRADES_FILE, {"trades": []})
    if not STATS_FILE.exists():
        _write_json(STATS_FILE, {
            "cash_balance":  INITIAL_CASH_BALANCE,
            "total_invested": 0.0,
            "total_pnl":     0.0,
            "daily_pnl":     0.0,
            "last_updated":  utc_now(),
        })


def _scanner_watchlist() -> list[str]:
    """Read the unified scanner watchlist (single source of truth)."""
    try:
        from bots.scanner_bot.scanner import get_watchlist
        wl = get_watchlist()
        coins = wl.get("coins", [])
        return [str(c).upper().strip() for c in coins if str(c).strip()]
    except Exception:
        # NF-8: watchlist read failed — snapshot() returns empty watchlist rather than crashing.
        logger.exception("PMB _scanner_watchlist: failed to read scanner watchlist — returning []")
        return []


def load_positions() -> list[dict]:
    ensure_storage()
    data = _read_json(POSITIONS_FILE, {"positions": []})
    return data.get("positions", []) if isinstance(data, dict) else []


def save_positions(positions: list[dict]) -> None:
    with _positions_lock:
        _write_json(POSITIONS_FILE, {"positions": positions})


def load_trades() -> list[dict]:
    ensure_storage()
    data = _read_json(TRADES_FILE, {"trades": []})
    return data.get("trades", []) if isinstance(data, dict) else []


def save_trades(trades: list[dict]) -> None:
    with _trades_lock:
        _write_json(TRADES_FILE, {"trades": trades})


def load_stats() -> dict:
    ensure_storage()
    data = _read_json(STATS_FILE, {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("cash_balance",  INITIAL_CASH_BALANCE)
    data.setdefault("total_invested", 0.0)
    data.setdefault("total_pnl",     0.0)
    data.setdefault("daily_pnl",     0.0)
    data.setdefault("last_updated",  utc_now())
    return data


def save_stats(stats: dict) -> None:
    with _stats_lock:
        stats = dict(stats)
        stats["last_updated"] = utc_now()
        _write_json(STATS_FILE, stats)


def update_stats(fn: Callable[[dict], None]) -> dict:
    """Atomically load, modify, and save stats under _stats_lock.

    Holds *_stats_lock* for the ENTIRE read → modify → write sequence so
    that concurrent callers cannot produce a lost-update on cash_balance or
    any other stats field.

    ``fn(stats)`` is called with the current (normalised) stats dict and
    must mutate it in-place.  ``fn`` must not call ``load_stats`` or
    ``save_stats`` (that would deadlock on *_stats_lock*).

    Returns the saved stats dict (after ``last_updated`` is stamped).

    This is the ONLY safe way to mutate stats from the trading engine.
    Read-only callers (``snapshot``, ``validate_signal``) may continue to
    use ``load_stats()``.
    """
    with _stats_lock:
        ensure_storage()
        data = _read_json(STATS_FILE, {})
        if not isinstance(data, dict):
            data = {}
        data.setdefault("cash_balance",   INITIAL_CASH_BALANCE)
        data.setdefault("total_invested",  0.0)
        data.setdefault("total_pnl",       0.0)
        data.setdefault("daily_pnl",       0.0)
        data.setdefault("last_updated",    utc_now())
        fn(data)
        data["last_updated"] = utc_now()
        _write_json(STATS_FILE, data)
        return dict(data)


def get_open_positions() -> list[dict]:
    return [p for p in load_positions() if str(p.get("status", "")).upper() == "OPEN"]


# ── Trade History filtering ────────────────────────────────────────────────
# IMPORTANT: The raw trade log (``trades.json`` / ``load_trades()``) records
# *every* position event — entries (BASE_BUY, DIP_BUY_N) as well as exits
# (PARTIAL_SELL_N, STOP_LOSS, TRAILING_STOP, MANUAL_SELL, FINAL_SELL). That
# full log is the "open trade log" and is used internally (e.g. to look up a
# position's entry price / entry time for enrichment).
#
# "PMB Trade History" on the dashboard must show ONLY completed/closed trades
# — i.e. records where the position (or partial slice of it) has actually
# exited. It must NEVER show entry-only actions like BASE_BUY or DIP_BUY_N,
# even though those rows technically live in the same trades.json file.
#
# Open trade log (load_trades()) != closed trade history (get_closed_trades()).
# Use ``get_closed_trades()`` / the ``closed_trades`` key from ``snapshot()``
# for any UI that should only display finished trades.

_ENTRY_ONLY_ACTIONS = ("BASE_BUY", "DIP_BUY")
_CLOSED_EXIT_ACTIONS = (
    "FINAL_SELL",
    "STOP_LOSS",
    "TRAILING_STOP",
    "MANUAL_SELL",
    "PARTIAL_SELL_TP",
    "PARTIAL_SELL",  # covers PARTIAL_SELL_<n> variants emitted by trading_engine
)


def _is_closed_trade(t: dict) -> bool:
    """True only for records that represent a genuinely completed (exited) trade.

    Excludes entry transactions (BASE_BUY, DIP_BUY_N) and any record whose
    position is still OPEN — those belong in "PMB Open Positions", not
    "PMB Trade History".
    """
    action = str(t.get("action", "")).upper()
    status = str(t.get("status", "")).upper()

    # Never show entry-only actions, regardless of status.
    if action.startswith(_ENTRY_ONLY_ACTIONS):
        return False

    # Must be closed — an OPEN status means the position (or remainder of it)
    # has not exited yet, so it belongs on the Open Positions table only.
    if status != "CLOSED":
        return False

    # Recognised closed-exit actions, or any other action as long as the
    # record is explicitly marked CLOSED (covers PARTIAL_SELL_<n> naming and
    # any future exit action names not yet enumerated above).
    return status == "CLOSED"


def get_closed_trades() -> list[dict]:
    """Return only completed trades for the Trade History view.

    Open trade log != closed trade history: this filters out BASE_BUY /
    DIP_BUY entry rows and anything still OPEN, keeping only rows where the
    trade genuinely exited (FINAL_SELL, STOP_LOSS, TRAILING_STOP,
    MANUAL_SELL, PARTIAL_SELL_TP, or any action with status == "CLOSED").
    """
    return [t for t in load_trades() if _is_closed_trade(t)]


def snapshot() -> dict:
    from .config import BOT_MODE
    positions   = load_positions()
    trades      = load_trades()  # open trade log — includes entries + exits
    stats       = load_stats()
    open_pos    = [p for p in positions if str(p.get("status", "")).upper() == "OPEN"]
    # "closed_trades" here must be the *filtered* trade history, not the raw
    # trade log, so BASE_BUY / DIP_BUY / still-OPEN rows never reach the
    # dashboard's "PMB Trade History" table.
    closed_trades = get_closed_trades()
    return {
        "status":         "INTEGRATED",
        "mode":           BOT_MODE,
        "open_positions": open_pos,
        "closed_trades":  closed_trades[-50:],
        "daily_pnl":      round(float(stats.get("daily_pnl",     0.0)), 4),
        "total_pnl":      round(float(stats.get("total_pnl",     0.0)), 4),
        "trade_amount":   float(BASE_BUY),
        "cash_balance":   round(float(stats.get("cash_balance",  0.0)), 4),
        "watchlist":      _scanner_watchlist(),
        "last_updated":   stats.get("last_updated"),
    }


ensure_storage()
