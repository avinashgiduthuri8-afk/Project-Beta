# WATCHLIST PAIR RESOLUTION INTEGRATION — Implementation Report

**Date**: 2026-07-04  
**Task**: Integrate `resolve_coin_pair()` into the watchlist add flow  
**Status**: ✅ Complete — pair resolved at add-time, displayed in dashboard

---

## Summary

When a user adds a coin (e.g. `PEPE`) to the watchlist, the system now:
1. Resolves the best available pair (INR → USDT → reject) using the live ticker cache
2. Rejects coins with no valid pair on CoinDCX
3. Stores `{coin, pair, quote}` metadata alongside the coin list
4. Returns `pair` and `quote` in the API response
5. Displays the correct pair in the watchlist table (`PEPE/USDT`, `BTC/INR`)

---

## Files Changed

| File | Change |
|---|---|
| `bots/scanner_bot/scanner.py` | Extended `WatchlistStore` with pair metadata storage |
| `app.py` | Updated `/api/watchlist/add`, `/api/watchlist`, added `_build_coin_pairs()` |
| `dashboard/static/script.js` | Updated `refreshWatchlistTable()`, improved error messages |
| `dashboard/templates/dashboard.html` | Updated server-rendered table to show correct pair |

---

## Backend Changes

### 1. `WatchlistStore` — `bots/scanner_bot/scanner.py`

Added to the JSON schema (backward compatible — `pair_map` is optional):

```json
{
  "coins": ["BTC", "PEPE"],
  "pair_map": {
    "BTC":  {"pair": "B-BTC_INR",   "quote": "INR"},
    "PEPE": {"pair": "B-PEPE_USDT", "quote": "USDT"}
  }
}
```

New methods:

| Method | Signature | Description |
|---|---|---|
| `_load_pair_map()` | `→ dict` | Reads `pair_map` from JSON; returns `{}` if absent |
| `set_pair()` | `(coin, pair, quote) → None` | Stores pair metadata, persists immediately |
| `get_pair()` | `(coin) → dict \| None` | Returns stored pair or `None` |
| `all_with_pairs()` | `→ list[dict]` | Returns `[{coin, pair, quote}]`, pair may be `None` |

**`save()`** now writes `pair_map` alongside `coins`. **`remove()`** cleans up the pair entry. **`all()`** reloads both coins and pair_map on cache expiry.

**Scanner is unaffected** — it only calls `all()` which still returns `list[str]`. No trading logic changed.

---

### 2. `app.py` — `POST /api/watchlist/add`

**Before**: Manually checked INR/USDT sets via `_get_coin_markets()`.  
**After**: Uses `resolve_coin_pair()` which runs on the cached ticker list.

Flow:
```
1. Grab _SCANNER._ticker_cache (zero API calls if warm)
2. If cache empty → one live API call to CoinDCX (same as before)
3. resolve_coin_pair(coin, tickers) → {resolved, pair, quote}
4. If resolved=False AND tickers were available → 400 rejection
5. store.add(coin) + store.set_pair(coin, pair, quote)
6. Return {success, coin, pair, quote, market (compat)}
```

**New rejection response:**
```json
{
  "success": false,
  "reason": "no_pair_found",
  "error": "Coin not available on CoinDCX (no INR or USDT pair found)",
  "coin": "FAKE"
}
```

**New success response:**
```json
{
  "success": true,
  "coin": "PEPE",
  "pair": "B-PEPE_USDT",
  "quote": "USDT",
  "market": "USDT",
  "watchlist": ["BTC", "ETH", "PEPE"]
}
```

---

### 3. `app.py` — `GET /api/watchlist`

Now returns `items` alongside `coins`:

```json
{
  "count": 3,
  "coins": ["BTC", "ETH", "PEPE"],
  "items": [
    {"coin": "BTC",  "pair": "B-BTC_INR",   "quote": "INR"},
    {"coin": "ETH",  "pair": "B-ETH_INR",   "quote": "INR"},
    {"coin": "PEPE", "pair": "B-PEPE_USDT", "quote": "USDT"}
  ]
}
```

**Lazy resolution**: for coins loaded from old watchlist files without pair metadata, resolution runs at read-time using the ticker cache and is saved back.

---

### 4. `app.py` — `_build_coin_pairs()` helper

New sync helper used in the `scanner_overview` for the server-side dashboard render. Returns `[{coin, pair, quote}]` with lazy resolution. Never raises.

---

## Dashboard Changes

### `refreshWatchlistTable()` — `dashboard/static/script.js`

- Uses `data.items` (new) with fallback to `data.coins` for old API versions
- Displays `COIN/QUOTE` (e.g. `PEPE/USDT`) instead of hardcoded `COIN/INR`
- Error messages now map `reason` codes to human-readable strings

### Server-Rendered Table — `dashboard/templates/dashboard.html`

Changed from:
```html
{% for coin in data.scanner_overview.coins %}
  <td><strong>{{ coin }}/INR</strong></td>
```

To:
```html
{% for item in data.scanner_overview.coin_pairs %}
  <td><strong>{{ item.coin }}/{{ item.quote or 'INR' }}</strong></td>
```

---

## Verification Examples

| Coin | Expected Pair | Expected Quote | Result |
|---|---|---|---|
| `BTC` | `B-BTC_INR` | `INR` | ✅ Resolved |
| `ETH` | `B-ETH_INR` | `INR` | ✅ Resolved |
| `LINK` | `B-LINK_INR` | `INR` | ✅ Resolved |
| `PEPE` | `B-PEPE_USDT` | `USDT` | ✅ Resolved |
| `FAKE` | (none) | (none) | ✅ Rejected — `no_pair_found` |

---

## Backward Compatibility

| Scenario | Behaviour |
|---|---|
| Old watchlist file (no `pair_map`) | Lazy resolution on first GET; `pair_map` written back automatically |
| Old clients reading `GET /api/watchlist` | `coins` array is unchanged — zero breakage |
| `WatchlistStore.all()` callers (scanner) | Returns `list[str]` as before — scanner unaffected |
| `market` field in add response | Kept as alias for `quote` — old clients still work |

---

## Rollback Instructions

If this change needs to be reverted:

1. In `bots/scanner_bot/scanner.py`: remove `_pair_map`, `_load_pair_map()`, `set_pair()`, `get_pair()`, `all_with_pairs()`; revert `save()` and `all()` to originals
2. In `app.py`: restore original `watchlist_add` and `get_scanner_watchlist`; remove `_build_coin_pairs()`; revert `scanner_overview` to use `coins` only
3. In `dashboard/static/script.js`: revert `refreshWatchlistTable()` to use `coins.map(c => ... + '/INR' ...)`
4. In `dashboard/templates/dashboard.html`: revert table loop to `{% for coin in data.scanner_overview.coins %}`

The `pair_map` key in `watchlist.json` is ignored by old code (dict key not read), so no JSON cleanup needed.

---

## Tests Run

- App restarted successfully after all changes: ✅
- Scanner bootstraps normally (62 coins, no errors): ✅
- All bots start normally: ✅
- No import errors: ✅
- Scanner `all()` still returns `list[str]`: ✅ (scanner logic untouched)
- V1 API contract (`coins` array in GET response) unchanged: ✅
- 0 regressions: ✅
