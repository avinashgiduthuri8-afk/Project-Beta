# VGX Storage Path Unification

**Status:** Complete  
**Date:** 2026-07-04

---

## Problem

Two independent path calculations existed for the same file (`TradingBotCrypto.json`):

| Consumer | Path expression | Result |
|---|---|---|
| `bots/volatile_gridX/config.py` | `os.path.join("storage", "TradingBotCrypto.json")` | Relative — resolves differently depending on CWD |
| `app.py` | `os.path.join(os.path.dirname(__file__), "bots", "volatile_gridX", "storage", "TradingBotCrypto.json")` | Absolute from `app.py`'s directory |

If the process was launched from any directory other than the project root, the VGX bot and the dashboard would resolve different files. The risk engine read positions via `storage.py`, which imported `STORAGE_FILE` from `config.py` (relative path), creating a third inconsistency.

---

## Changes Made

### `bots/volatile_gridX/config.py`

- Added `import pathlib as _pathlib` and computed `_VGX_ROOT = _pathlib.Path(__file__).resolve().parent` — an absolute path anchored to the `config.py` file itself, not to the process CWD.
- Changed `STORAGE_DIR`, `STORAGE_FILE`, and `STORAGE_BACKUP` to be derived from `_VGX_ROOT`, making them absolute on first import.
- Added `get_vgx_storage_file()` — a canonical helper that returns `STORAGE_FILE`.  All callers should use this helper instead of building paths manually.

```python
_VGX_ROOT      = _pathlib.Path(__file__).resolve().parent
STORAGE_DIR    = str(_VGX_ROOT / "storage")
STORAGE_FILE   = str(_VGX_ROOT / "storage" / f"{PROJECT_NAME}.json")
STORAGE_BACKUP = str(_VGX_ROOT / "storage" / f"{PROJECT_NAME}_backup.json")

def get_vgx_storage_file() -> str:
    """Canonical absolute path to TradingBotCrypto.json. Use this everywhere."""
    return STORAGE_FILE
```

### `app.py`

- Removed the independently-hardcoded `_VGX_STORAGE_FILE` constant.
- Added `from bots.volatile_gridX.config import get_vgx_storage_file as _get_vgx_storage_file`.
- `vgx_snapshot()` now calls `_get_vgx_storage_file()` at call time (not at module load time).
- Updated the docstring to reflect the change.

---

## Why This Is Safe

- `bots/volatile_gridX/storage.py` imports `STORAGE_FILE` via `from .config import *` — it automatically gets the now-absolute path with no code change.
- `bots/risk_engine/engine.py` reads VGX positions via `storage.get_open_positions()`, which reads `STORAGE_FILE` from config — again benefiting automatically.
- No storage file was moved; only the path construction was fixed.

---

## Verification

```
STORAGE_FILE: /home/runner/workspace/bots/volatile_gridX/storage/TradingBotCrypto.json
get_vgx_storage_file(): /home/runner/workspace/bots/volatile_gridX/storage/TradingBotCrypto.json
os.path.isabs(STORAGE_FILE): True
```

Full test suite: **542 passed, 0 failures** (no regressions).
