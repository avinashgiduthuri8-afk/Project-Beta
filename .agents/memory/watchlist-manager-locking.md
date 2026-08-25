---
name: Watchlist manager locking
description: Locking design for bots/shared/watchlist_manager.py — RLock, once-guard, atomic writes
---

## Rule
All read-modify-write paths in `watchlist_manager.py` must hold `_watchlist_lock` (RLock).
`ensure_migration()` must use the double-checked locking pattern with `_migration_once_lock` (plain Lock).
File writes must use `os.replace()` on a temp file, not a plain `open(..., "w")`.

**Why:**
- `add_coin()`/`remove_coin()` had an unprotected read-then-write: two concurrent callers would both read the same list, both add/remove, and the second write would clobber the first.
- `_migrate_old_bot_watchlists()` is called by `ensure_migration()` which itself was unguarded, so two threads could both see `_MIGRATION_RESULT is None` and run migration twice.
- Plain `open(..., "w")` leaves a partial-file window if a reader or concurrent writer hits the file mid-write; `os.replace()` on a temp file is atomic on Linux.

**How to apply:**
- `_watchlist_lock = threading.RLock()` — RLock (not Lock) because `add_coin()` holds it while `_migrate_old_bot_watchlists()` (called transitively) also acquires it on first run.
- `_migration_once_lock = threading.Lock()` — plain Lock, guards only the check-and-set of `_MIGRATION_RESULT` inside `ensure_migration()`.
- `ensure_migration()` pattern: fast-path read without lock → acquire `_migration_once_lock` → re-check → run migration.
- `add_coin()`/`remove_coin()`: call `ensure_migration()` **before** acquiring `_watchlist_lock` to avoid first-run re-entrant deadlock.
- `_write_scanner_watchlist()`: write to `dest + ".wm_tmp"`, then `os.replace(tmp, dest)`.
- Timeout: `_watchlist_lock.acquire(timeout=_LOCK_TIMEOUT)` with `try/finally release()`; log warning + raise `RuntimeError` on timeout.
