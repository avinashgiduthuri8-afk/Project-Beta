"""
PROJECT-ALPHA — Unified Scanner Watchlist Manager

V1 Architecture: Scanner is the single source of truth.
All bots read scanner watchlist directly — no per-bot watchlists.
"""

import json
import logging
import os
import threading
import time

_logger = logging.getLogger("watchlist_manager")

# Single RLock for all read-modify-write operations on the scanner watchlist
# file.  RLock (not Lock) so that add_coin()/remove_coin() can hold the lock
# while calling ensure_migration() on the very first invocation, which in turn
# calls _migrate_old_bot_watchlists() which also acquires the lock.
# Writes are infrequent and the file is small — one global lock is sufficient.
_watchlist_lock = threading.RLock()
_LOCK_TIMEOUT = 5.0  # seconds before a timed-out acquire is treated as an error

# Plain Lock (not RLock) that guards the check-and-set of _MIGRATION_RESULT
# inside ensure_migration().  Two threads arriving simultaneously must not
# both observe _MIGRATION_RESULT is None and both run the migration.
_migration_once_lock = threading.Lock()

# Scanner watchlist is the single source of truth
_SCANNER_WATCHLIST_FILE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "scanner_bot", "data", "watchlist.json"
)

# Old bot watchlist files for one-time migration
_OLD_BOT_FILES = {
    "vgx": os.path.join(os.path.dirname(os.path.dirname(__file__)), "volatile_gridX", "data", "watchlist.json"),
    "pmb": os.path.join(os.path.dirname(os.path.dirname(__file__)), "pmb_bot", "data", "watchlist.json"),
    "mtb": os.path.join(os.path.dirname(os.path.dirname(__file__)), "mtb_bot", "data", "watchlist.json"),
}

_QUOTE_SUFFIXES = ("USDT", "BUSD", "INR", "BTC")


def _normalize_coin(coin: str) -> str:
    """Strip known quote suffixes so BTCINR -> BTC, ETHUSDT -> ETH, etc."""
    c = str(coin).upper().strip()
    for suffix in _QUOTE_SUFFIXES:
        if c.endswith(suffix) and len(c) > len(suffix):
            return c[:-len(suffix)]
    return c


def _read_scanner_watchlist() -> list:
    """Read the scanner watchlist from disk."""
    if not os.path.exists(_SCANNER_WATCHLIST_FILE):
        return []
    try:
        with open(_SCANNER_WATCHLIST_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("coins", [])
    except (json.JSONDecodeError, OSError):
        return []


def _write_scanner_watchlist(coins: list) -> None:
    """Write the scanner watchlist to disk atomically (temp-file + rename).

    Atomic rename means a concurrent reader always sees either the old or the
    new file content — never a half-written file.  This also makes concurrent
    cross-module writes (e.g. WatchlistStore.save() vs add_coin()) at worst
    last-write-wins rather than file corruption.
    """
    dest = _SCANNER_WATCHLIST_FILE
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".wm_tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"coins": sorted(set(coins)), "updated_at": int(time.time())}, f, indent=2)
    os.replace(tmp, dest)


def _migrate_old_bot_watchlists() -> list:
    """
    One-time migration: if old bot watchlist files exist, merge their unique
    coins into the scanner watchlist, then log the migration.

    Called at most once per process (guarded by ensure_migration).  The
    read-modify-write is protected by _watchlist_lock (RLock) so a concurrent
    add_coin() that also holds the lock doesn't race with this write.
    """
    migrated_coins = set()
    sources = []
    for bot, path in _OLD_BOT_FILES.items():
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                coins = data.get("coins", [])
                for c in coins:
                    coin = _normalize_coin(c) if isinstance(c, str) else _normalize_coin(str(c))
                    if coin:
                        migrated_coins.add(coin)
                sources.append(bot)
            except (json.JSONDecodeError, OSError):
                pass

    if not migrated_coins:
        return []

    # Merge with existing scanner watchlist — hold the lock for the full
    # read-modify-write so concurrent writers don't clobber each other.
    acquired = _watchlist_lock.acquire(timeout=_LOCK_TIMEOUT)
    if not acquired:
        _logger.warning("_migrate_old_bot_watchlists: lock timeout — migration skipped")
        return []
    try:
        existing = set(_read_scanner_watchlist())
        new_coins = migrated_coins - existing
        if new_coins:
            _write_scanner_watchlist(list(existing | migrated_coins))
    finally:
        _watchlist_lock.release()

    return sorted(new_coins)


# Run migration on first import (only when old files exist)
_MIGRATION_RESULT = None


def ensure_migration():
    """Run one-time migration of old bot watchlists into scanner watchlist.

    Thread-safe once-guard: _migration_once_lock ensures that two threads
    arriving simultaneously when _MIGRATION_RESULT is still None cannot both
    run _migrate_old_bot_watchlists().  After the first run, the fast-path
    (result already set) never acquires the lock.
    """
    global _MIGRATION_RESULT
    if _MIGRATION_RESULT is not None:          # fast-path — no lock needed
        return _MIGRATION_RESULT
    with _migration_once_lock:
        if _MIGRATION_RESULT is not None:      # re-check after acquiring
            return _MIGRATION_RESULT
        _MIGRATION_RESULT = _migrate_old_bot_watchlists()
        return _MIGRATION_RESULT


def get_scanner_universe() -> dict:
    """Return the unified scanner universe (single source of truth)."""
    ensure_migration()
    return {"coins": _read_scanner_watchlist()}


def add_coin(coin: str) -> dict:
    """Add a coin to the scanner watchlist.

    Thread-safe: the full read-modify-write is held under _watchlist_lock.
    Raises RuntimeError if the lock cannot be acquired within _LOCK_TIMEOUT
    seconds (caller receives an error; no silent partial write).
    """
    # ensure_migration() is idempotent after the first run — call it before
    # acquiring the lock so there is no deadlock on the very first invocation
    # (the migration itself acquires the RLock internally).
    ensure_migration()
    coin = _normalize_coin(coin)
    acquired = _watchlist_lock.acquire(timeout=_LOCK_TIMEOUT)
    if not acquired:
        _logger.warning(
            "add_coin: lock acquisition timed out for coin=%s — write skipped", coin
        )
        raise RuntimeError(f"watchlist lock timeout — add_coin({coin!r}) skipped")
    try:
        coins = set(_read_scanner_watchlist())
        if coin not in coins:
            coins.add(coin)
            _write_scanner_watchlist(list(coins))
        return {"coins": sorted(coins)}
    finally:
        _watchlist_lock.release()


def remove_coin(coin: str) -> dict:
    """Remove a coin from the scanner watchlist.

    Thread-safe: the full read-modify-write is held under _watchlist_lock.
    Raises RuntimeError if the lock cannot be acquired within _LOCK_TIMEOUT
    seconds (caller receives an error; no silent partial write).
    """
    ensure_migration()
    coin = _normalize_coin(coin)
    acquired = _watchlist_lock.acquire(timeout=_LOCK_TIMEOUT)
    if not acquired:
        _logger.warning(
            "remove_coin: lock acquisition timed out for coin=%s — write skipped", coin
        )
        raise RuntimeError(f"watchlist lock timeout — remove_coin({coin!r}) skipped")
    try:
        coins = set(_read_scanner_watchlist())
        if coin in coins:
            coins.remove(coin)
            _write_scanner_watchlist(list(coins))
        return {"coins": sorted(coins)}
    finally:
        _watchlist_lock.release()
