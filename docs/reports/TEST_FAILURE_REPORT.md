# Test Failure Report — Pre-Existing Failures Fixed

**Status:** Complete  
**Date:** 2026-07-04  
**Before:** 532 passed / 10 failures  
**After:** 542 passed / 0 failures

---

## Failure Categories

### Category A — Mock Issue (5 tests)

**Root cause:** Tests that need to patch `time.sleep` to skip rate-limiting delays were patching the entire `time` module (`patch("bots.scanner_bot.scanner.time")`).  When the whole module is replaced with a `MagicMock`, `time.monotonic()` (called inside `_limited_get`) returns a `MagicMock` object instead of a `float`.  The expression `_RATE_MIN_GAP_S - (MagicMock() - 0.0)` then evaluates to a `MagicMock`, and `if wait > 0` raises `TypeError: '>' not supported between instances of 'MagicMock' and 'int'`.

**Fix:** Add `mock_time.monotonic.return_value = 0.0` alongside the existing `mock_time.sleep = MagicMock()`.  This makes `_limited_get` see a sensible monotonic timestamp, so the rate-limit gap calculation produces a float and the `if wait > 0` guard works correctly.  Sleep is still skipped.

**Tests fixed:**

| File | Test |
|---|---|
| `test_sp1_1_bootstrap.py` | `TestFetchBootstrapCandles::test_retries_on_timeout_then_succeeds` |
| `test_sp1_1_bootstrap.py` | `TestFetchBootstrapCandles::test_connection_error_is_retried` |
| `test_sp1_2_live_feed.py` | `TestCoinDCXPublicClientFetchTickers::test_retries_on_timeout_then_succeeds` |
| `test_sp1_2_live_feed.py` | `TestCoinDCXPublicClientFetchTickers::test_all_retries_exhausted_raises` |
| `test_sp1_2_live_feed.py` | `TestCoinDCXPublicClientFetchTickers::test_connection_error_is_retried` |

---

### Category B — Legacy test with wrong expectation (1 test)

**Root cause:** `TestCheckCandlesConnectivity::test_returns_false_on_non_200_response` asserted that `_check_candles_connectivity()` returns `False` for a 503 response.  But the function's documented contract explicitly states: *"any HTTP response counts as reachable because a server-side validation error (4xx/5xx) still proves the network path is open — only network-level exceptions mean the host is unreachable."*  The function was correct; the test expectation was wrong.

**Fix:** Renamed test to `test_returns_true_on_non_200_response`, updated docstring to quote the function's contract, changed assertion from `result is False` to `result is True`.

| File | Old test name | New test name |
|---|---|---|
| `test_sp6_and_prod_fixes.py` | `test_returns_false_on_non_200_response` | `test_returns_true_on_non_200_response` |

---

### Category C — Port assumption (2 tests)

**Root cause:** `TestScannerApiUrlDefault` tested that the default `SCANNER_API_URL` for MTB and PMB configs contained port `8080`.  The configs were updated (in a prior session) to default to port `5000` (matching the Replit-hosted app), but the test expectations were never updated.

**Fix:** Updated both tests to expect `5000`, assert `5000` is present, and assert `8080` is absent.  Test names updated to reflect the correct expected value.

| File | Old test name | New test name |
|---|---|---|
| `test_sp6_and_prod_fixes.py` | `test_mtb_scanner_api_url_defaults_to_8080` | `test_mtb_scanner_api_url_defaults_to_5000` |
| `test_sp6_and_prod_fixes.py` | `test_pmb_scanner_api_url_defaults_to_8080` | `test_pmb_scanner_api_url_defaults_to_5000` |

---

### Category D — Environment variable leak (2 tests)

**Root cause:** `test_mtb_disabled_by_default` and `test_pmb_disabled_by_default` called `os.getenv("MTB_ENABLED", "false")` directly without isolating the environment.  In the Replit environment `MTB_ENABLED=true` and `PMB_ENABLED=true` are set (as Replit secrets/env vars), causing both tests to always fail.  These tests were designed to verify the code's *default* behaviour, not the operator's runtime configuration.

**Fix:** Wrapped each test body in `patch.dict(os.environ, {}, clear=False)` + `os.environ.pop("<VAR>", None)` to simulate a clean environment where the variable is absent.  The assertion then correctly exercises the `os.getenv(..., "false")` default.

| File | Test |
|---|---|
| `test_watchlist_removal_verification.py` | `TestBotFilters::test_mtb_disabled_by_default` |
| `test_watchlist_removal_verification.py` | `TestBotFilters::test_pmb_disabled_by_default` |

---

## Summary

| Category | Count | Fix strategy |
|---|---|---|
| Mock issue (`time.monotonic` returning MagicMock) | 5 | Add `mock_time.monotonic.return_value = 0.0` |
| Legacy test with stale expectation | 1 | Update assertion to match documented behavior |
| Port assumption (8080 vs 5000) | 2 | Update expected port in assertions |
| Env var leak (REPLIT env polluting test) | 2 | Isolate env with `patch.dict` + `pop` |

**No production code was changed** for any Category A / B / C / D fix — all changes are confined to test files.
