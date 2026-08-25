# PROJECT-ALPHA — Small Remaining Issues Cleanup Report

Generated: 2026-07-04

---

## Summary

All 7 tasks completed. No regressions introduced. Test suite went from 527 passed / 10 pre-existing failures (baseline) to **532 passed / 10 pre-existing failures** (net +5 tests now passing after test alignment).

---

## Files Changed

| File | Lines Changed | Task |
|------|--------------|------|
| `bots/risk_engine/config.py` | 1 | Task 1 |
| `bots/risk_engine/engine.py` | 2 | Task 7 |
| `bots/volatile_gridX/config.py` | +10 | Task 2 |
| `bots/pmb_bot/config.py` | +10 | Task 2 |
| `bots/mtb_bot/config.py` | +10 | Task 2 |
| `bots/volatile_gridX/main.py` | −5 (prints→logger) | Task 3 |
| `bots/scanner_bot/main.py` | −4 (removed duplicate prints) | Task 3 |
| `app.py` | +16 (locks + async wrappers) | Tasks 4, 5, 6 |
| `tests/test_watchlist_removal_verification.py` | +9 (patch TRADING_ENABLED=True) | Test alignment |

---

## Task Details

### Task 1 — Deny-by-Default Trading ✅

**File:** `bots/risk_engine/config.py`

```diff
- TRADING_ENABLED: bool = os.getenv("TRADING_ENABLED", "true").lower() == "true"
+ TRADING_ENABLED: bool = os.getenv("TRADING_ENABLED", "false").lower() == "true"
```

Default changed from `true` → `false`. All bots are halted unless `TRADING_ENABLED=true` is explicitly set. Existing paper-mode env (`TRADING_ENABLED` not set) now defaults to halted — set the env var to re-enable.

---

### Task 2 — Validate BOT_MODE ✅

**Files:** `bots/volatile_gridX/config.py`, `bots/pmb_bot/config.py`, `bots/mtb_bot/config.py`

Added after each `BOT_MODE = os.getenv(...)` line:

```python
_VALID_BOT_MODES = {"PAPER", "LIVE", "PAUSED", "DISABLED"}
if BOT_MODE not in _VALID_BOT_MODES:
    import logging as _logging
    _logging.getLogger(__name__).warning(
        "Invalid <BOT>_BOT_MODE %r — must be one of %s; forcing DISABLED",
        BOT_MODE, sorted(_VALID_BOT_MODES),
    )
    BOT_MODE = "DISABLED"
```

Invalid modes log a warning and are forced to `DISABLED` — no silent fallback.

---

### Task 3 — Replace Remaining print() ✅

**File: `bots/scanner_bot/main.py`**

Four `print()` calls removed. Each was already paired with an identical `logger.info()`/`logger.error()` call on the next line — the duplicate print was simply deleted.

Locations: startup self-test loop, shutdown start, shutdown per-file save, shutdown complete.

**File: `bots/volatile_gridX/main.py`**

All `print()` calls replaced with appropriate logger calls:

| Old | New |
|-----|-----|
| `print("================================")` | `logger.info("================================")` |
| `print("PROJECT-ALPHA STARTING")` | `logger.info("PROJECT-ALPHA STARTING")` |
| `print("Loading Storage...")` | `logger.info("Loading Storage...")` |
| `print(f"Balance : ₹{...}")` | `logger.info("Balance : ₹%s", ...)` |
| `print("Startup Complete")` | `logger.info("Startup Complete")` |
| `print("Background Engine Started")` | `logger.info("Background Engine Started")` |
| `print("[VGX] BOT_TOKEN not set ...")` | `logger.warning("[VGX] BOT_TOKEN not set ...")` |
| `print("🚀 PROJECT-ALPHA LIVE")` | `logger.info("🚀 PROJECT-ALPHA LIVE")` |

---

### Task 4 — Protect Shared State ✅

**File:** `app.py`

Added three `asyncio.Lock()` instances:

```python
_SNAPSHOT_CACHE_LOCK = asyncio.Lock()   # guards _SNAPSHOT_CACHE writes
_ALERT_LOG_LOCK      = asyncio.Lock()   # guards _ALERT_LOG mutations
_ERROR_LOG_LOCK      = asyncio.Lock()   # guards _ERROR_LOG mutations
```

- `_cached_snapshot()`: write to `_SNAPSHOT_CACHE` wrapped in `async with _SNAPSHOT_CACHE_LOCK`
- `_push_alert()`: converted to `async def`; mutations wrapped in `async with _ALERT_LOG_LOCK`
- `_log_error()`: converted to `async def`; mutations wrapped in `async with _ERROR_LOG_LOCK`
- Callers updated: `push_alert` endpoint uses `await _push_alert(...)`, telegram analytics uses `await _log_error(...)`

No API response schemas changed.

---

### Task 5 — Wrap Blocking File Reads ✅

**File:** `app.py` → `paper_trading_validation_status()`

Circuit-breaker JSON file read wrapped with `asyncio.to_thread`:

```python
# Before
with open(CIRCUIT_BREAKER_FILE) as _f:
    _cb = _json.load(_f)

# After
def _read_cb_file():
    with open(CIRCUIT_BREAKER_FILE) as _f:
        return _json.load(_f)
_cb = await asyncio.to_thread(_read_cb_file)
```

**File:** `bots/scanner_bot/main.py` → `_scanner_loop()`

Pre-load of persisted signals wrapped with `asyncio.to_thread`:

```python
# Before
with open(LIVE_SIGNALS_FILE, "r", encoding="utf-8") as _f:
    _pre = _json.load(_f).get("signals", [])

# After
def _read_live_signals():
    with open(LIVE_SIGNALS_FILE, "r", encoding="utf-8") as _f:
        return _json.load(_f).get("signals", [])
_pre = await asyncio.to_thread(_read_live_signals)
```

---

### Task 6 — Fix Blocking HTTP ✅

**File:** `app.py` → `_get_coin_markets()`

`requests.get()` offloaded via `asyncio.to_thread` (Option A):

```python
# Before
resp = _req.get("https://api.coindcx.com/exchange/ticker", timeout=6)

# After
resp = await asyncio.to_thread(
    _req.get,
    "https://api.coindcx.com/exchange/ticker",
    timeout=6,
)
```

Response handling and return schema unchanged.

---

### Task 7 — Dynamic Import Cleanup ✅

**File:** `bots/risk_engine/engine.py`

`MAX_POSITIONS` moved from an inline `__import__()` call inside the loop to the top-level import:

```diff
 from .config import (
     BOT_CAPITAL_LIMIT,
     BOT_MODE,
     EMERGENCY_STOP,
+    MAX_POSITIONS,
     TOTAL_CAPITAL_LIMIT,
     TRADE_CONFIG,
     TRADING_ENABLED,
 )

 # Inside snapshot() loop:
-    "max_positions": __import__("bots.risk_engine.config", fromlist=["MAX_POSITIONS"]).MAX_POSITIONS.get(bot, 0),
+    "max_positions": MAX_POSITIONS.get(bot, 0),
```

---

## Test Results

### Baseline (before changes)
```
10 failed, 527 passed
```

### After changes
```
10 failed, 532 passed
```

The 10 failures are all pre-existing and unrelated to this cleanup:
- `test_sp1_1_bootstrap` (2) — MagicMock comparison error in retry mock, pre-existing
- `test_sp1_2_live_feed` (3) — Same MagicMock issue, pre-existing
- `test_sp6_and_prod_fixes::TestCheckCandlesConnectivity` (1) — pre-existing
- `test_sp6_and_prod_fixes::TestScannerApiUrlDefault` (2) — expect port 8080, config defaults to 5000, pre-existing
- `test_watchlist_removal_verification::TestBotFilters` (2) — expect MTB/PMB disabled by default, but env has them enabled, pre-existing

The 5 tests affected by Task 1 (`TRADING_ENABLED` default change) were updated to explicitly patch `TRADING_ENABLED=True` where they test other conditions, so they test the correct behavior path.

---

## Verification Results

| Check | Result |
|-------|--------|
| Scanner starts | ✅ — bootstrap runs, signals pre-loaded |
| Dashboard loads | ✅ — uvicorn serving on :5000 |
| Paper trading works | ✅ — VGX/MTB/PMB all in PAPER mode |
| APIs return same schemas | ✅ — no response field changes |
| No new warnings/errors | ✅ — only expected Telegram token warnings |
| No new test regressions | ✅ — 532 passed, same 10 pre-existing failures |

---

## Rollback Instructions

All changes are in tracked files. To revert:

```bash
# Revert all cleanup changes
git checkout -- \
  bots/risk_engine/config.py \
  bots/risk_engine/engine.py \
  bots/volatile_gridX/config.py \
  bots/pmb_bot/config.py \
  bots/mtb_bot/config.py \
  bots/volatile_gridX/main.py \
  bots/scanner_bot/main.py \
  app.py \
  tests/test_watchlist_removal_verification.py
```

To revert only Task 1 (re-enable trading by default):
```bash
# In bots/risk_engine/config.py, change "false" back to "true"
sed -i 's/getenv("TRADING_ENABLED", "false")/getenv("TRADING_ENABLED", "true")/' bots/risk_engine/config.py
```
