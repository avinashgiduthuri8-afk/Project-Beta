---
name: VGX grid storage ownership
description: Why grid_config/grid_coins were added to storage.py (not safe_storage.py) and the clobber risk.
---

## Rule
All VGX grid management state (`grid_config`, `grid_coins`) lives in `bots/volatile_gridX/storage.py` as module-level globals, persisted to `get_vgx_storage_file()` (`storage/TradingBotCrypto.json`).

**Why:** `safe_storage.py` uses a different file path (`data/TradingBotCrypto.json`) and a different lock (`thread_safety.storage_lock` / RLock). `storage.py` uses `_storage_lock` (threading.Lock) on the canonical file. If grid functions were placed in `safe_storage.py`, every call to `save_data()` in `storage.py` would clobber the new keys because `save_data()` builds a fixed-key payload and overwrites the whole file.

**Fix applied:** `grid_config` and `grid_coins` are now module globals in `storage.py`. `_normalise()`, `load_data()`, and `save_data()` all include them, so no save cycle can clobber them.

**How to apply:** Any future new persistent VGX state should follow the same pattern — add the global, add the default in `_normalise()`, load it in `load_data()`, write it in `save_data()`. Never split persistent VGX state across the two storage modules.

## Critical: _normalise() type coercion
`_normalise()` validates and coerces `grid_config` (must be dict) and `grid_coins` (must be non-empty list of strings) after `setdefault`. This prevents corrupt JSON from crashing callers. Any future fields added to this file should follow the same pattern.
