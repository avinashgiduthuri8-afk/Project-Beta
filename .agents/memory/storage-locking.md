---
name: Storage locking pattern
description: How threading locks are applied to all bot storage write functions in Project Alpha
---

Each storage module has one `threading.Lock()` per state file, acquired inside each `save_*` function body.

**Why:** Bot trading loops run in asyncio tasks backed by threads; concurrent saves without locking can corrupt JSON files mid-write.

**How to apply:**
- `bots/mtb_bot/storage.py`: `_positions_lock`, `_trades_lock`, `_stats_lock`
- `bots/pmb_bot/storage.py`: `_positions_lock`, `_trades_lock`, `_stats_lock`
- `bots/volatile_gridX/storage.py`: `_storage_lock` (one file covers everything)
- `bots/scanner_bot/scanner.py`: `_write_json_lock` (base writer), `_scanner_state_lock`, `_history_lock`, `_coin_perf_lock`, `_tier_acc_lock`
- Use `threading.Lock()` NOT `asyncio.Lock()` — callers are synchronous.
- Lock is held inside the function body, not at the call site.
