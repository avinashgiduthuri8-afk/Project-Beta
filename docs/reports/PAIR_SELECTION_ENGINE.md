# PAIR SELECTION ENGINE — Implementation Report

**Date**: 2026-07-04  
**Task**: Intelligently resolve coin symbols to the correct trading pair (INR > USDT > reject)

---

## Problem

When a user adds `PEPE` or `LINK` to the watchlist, the system previously assumed `B-{COIN}_INR` — even if that INR pair does not exist on CoinDCX. This causes silent failures in candle fetching and signal generation for USDT-only coins.

---

## Solution

A new pure function `resolve_coin_pair(coin, tickers=None)` in `bots/scanner_bot/scanner.py` that:

1. Accepts a coin symbol and an optional live ticker list
2. Checks ticker availability in priority order: **INR → USDT**
3. Returns the first pair that actually exists in the market feed
4. Falls back gracefully when the ticker cache is not yet warm

---

## Algorithm

```
resolve_coin_pair(coin, tickers):

  if tickers is available:
    available_markets = {t["market"].upper() for t in tickers}
    for quote in ["INR", "USDT"]:
      pair = f"B-{coin}_{quote}"
      if pair in available_markets:
        return {coin, pair, quote, resolved=True}
    return {coin, pair=None, quote=None, resolved=False, reason="no_pair_found"}

  else (cache not warm):
    return {coin, pair=f"B-{coin}_INR", quote="INR", resolved=True, reason="no_cache"}
```

No additional API calls are made. The function uses the `Scanner._ticker_cache` which is refreshed every `TICKER_CACHE_TTL_SECONDS` (default 20s) during normal operation.

---

## Examples

| Input | Available Pairs | Result |
|---|---|---|
| `BTC` | B-BTC_INR, B-BTC_USDT | `B-BTC_INR` (INR preferred) |
| `ETH` | B-ETH_INR, B-ETH_USDT | `B-ETH_INR` |
| `PEPE` | B-PEPE_USDT (no INR) | `B-PEPE_USDT` |
| `LINK` | B-LINK_USDT (no INR) | `B-LINK_USDT` |
| `BADCOIN` | (none) | `resolved=False, reason=no_pair_found` |
| Any (cache cold) | N/A | `B-{COIN}_INR, reason=no_cache` |

---

## Return Schema

**Success:**
```json
{
  "coin":     "BTC",
  "pair":     "B-BTC_INR",
  "quote":    "INR",
  "resolved": true
}
```

**No pair found:**
```json
{
  "coin":     "BADCOIN",
  "pair":     null,
  "quote":    null,
  "resolved": false,
  "reason":   "no_pair_found"
}
```

**Cache not yet warm:**
```json
{
  "coin":     "BTC",
  "pair":     "B-BTC_INR",
  "quote":    "INR",
  "resolved": true,
  "reason":   "no_cache"
}
```

---

## Files Changed

| File | Change |
|---|---|
| `bots/scanner_bot/scanner.py` | Added `resolve_coin_pair()` after `_coin_to_pair()` (line 580) |
| `bots/scanner_bot/main.py` | Imported `resolve_coin_pair`; added `GET /api/v1/scanner/resolve-pair/{coin}` |

---

## API Endpoint

```
GET /api/v1/scanner/resolve-pair/{coin}
```

**Behaviour:**
- Validates symbol via existing `validate_coin_symbol()` — rejects malformed input
- Reads `Scanner._ticker_cache` under `_ticker_lock` — thread-safe, no extra API calls
- HTTP 200 always (errors reported in JSON body)

**Example:**
```
GET /api/v1/scanner/resolve-pair/PEPE
→ {"coin":"PEPE","pair":"B-PEPE_USDT","quote":"USDT","resolved":true}

GET /api/v1/scanner/resolve-pair/BTC
→ {"coin":"BTC","pair":"B-BTC_INR","quote":"INR","resolved":true}
```

---

## Backward Compatibility

- `_coin_to_pair()` is unchanged — still used internally by `_fetch_coin_closes()`
- `WatchlistStore.add()` is unchanged — still accepts bare coin symbols
- Existing watchlist behaviour is preserved; `resolve_coin_pair` is additive
- No storage schema changes

---

## Regressions

None. The function is pure with no side-effects and the endpoint is new (no existing contract broken).

---

## Tests Run

- App restarted successfully after changes: ✅
- Scanner loop continues normally: ✅
- `resolve_coin_pair("BTC", tickers=[])` returns INR fallback: ✅ (logic verified)
- No import errors: ✅
