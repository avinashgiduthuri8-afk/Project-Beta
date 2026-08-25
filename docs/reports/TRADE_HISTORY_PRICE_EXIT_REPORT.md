# TRADE HISTORY — Current Price & Exit Reason Columns
**Date**: 2026-07-04  
**Task**: Add Current Price and Exit Reason columns to PMB and MTB closed trade tables  
**Status**: ✅ Complete

---

## Files Changed

| File | Change |
|---|---|
| `app.py` | Extended `_enrich_closed_trades()`, added `_get_current_prices_safe()`, updated `pull_state_payload()` |
| `dashboard/templates/dashboard.html` | PMB table: 9 → 11 cols; MTB table: 9 → 10 cols |
| `dashboard/static/style.css` | Added `.badge-green`, `.badge-red`, `.badge-gold`, `.badge-blue` modifier classes |

**No scanner logic changes. No trading logic changes. No storage schema changes.**

---

## API Response Example

`GET /api/v1/state` → `pmb_overview.closed_trades[]` and `mtb_overview.closed_trades[]`

### PMB closed trade (enriched)
```json
{
  "id":           "pos-abc123",
  "bot":          "PMB",
  "coin":         "PEPE",
  "symbol":       "B-PEPE_USDT",
  "action":       "STOP_LOSS",
  "status":       "CLOSED",
  "price":        0.000012,
  "amount":       95.40,
  "quantity":     7950000,
  "pnl":          -4.60,
  "timestamp":    "2026-07-04T04:11:22+00:00",
  "pnl_pct":      -4.60,
  "entry_price":  0.0000126,
  "holding_time": "2h 14m",
  "exit_reason":  "STOP_LOSS",
  "current_price": 0.0000119
}
```

### MTB closed trade (enriched)
```json
{
  "id":           "pos-def456",
  "bot":          "MTB",
  "coin":         "BTC",
  "symbol":       "B-BTC_INR",
  "action":       "SELL",
  "status":       "CLOSED",
  "price":        8750000.0,
  "amount":       8750.50,
  "quantity":     0.001,
  "pnl":          416.69,
  "return_pct":   5.0,
  "reason":       "TAKE_PROFIT",
  "timestamp":    "2026-07-04T03:55:10+00:00",
  "pnl_pct":      5.0,
  "entry_price":  8333310.0,
  "holding_time": "45m",
  "exit_reason":  "TAKE_PROFIT",
  "current_price": 8760000.0
}
```

---

## Backend Changes

### `_enrich_closed_trades(trades, all_trades, prices=None)` — `app.py`

New optional `prices: dict | None = None` parameter. All existing call sites that omit it continue to work unchanged (backward compatible).

**`exit_reason` derivation logic:**

| Input fields checked | Mapping |
|---|---|
| `action="STOP_LOSS"` or `reason="STOP_LOSS"` or `close_reason="STOP_LOSS"` | `STOP_LOSS` |
| `action="TAKE_PROFIT"` or `reason="TAKE_PROFIT"` | `TAKE_PROFIT` |
| `action="TRAILING_STOP"` | `TRAILING_STOP` |
| `action="MANUAL"` | `MANUAL` |
| `action` starts with `PARTIAL_SELL` (PMB partial sells) | `TAKE_PROFIT` |
| Anything else with `status=CLOSED` | `UNKNOWN` |

**`current_price` assignment:**
- Looks up `prices[coin]` from the pre-fetched dict
- Returns `None` if coin is absent or prices dict is empty
- Coin is extracted from `trade["coin"]` first, then parsed from `trade["symbol"]` as fallback

---

### `_get_current_prices_safe(coins)` — `app.py` (new async helper)

```
Priority order:
  1. scanner_main._SCANNER._ticker_cache  (zero I/O, instantaneous)
  2. CoinDCX public tickers API           (asyncio.to_thread, 5s timeout)
  3. None for each coin                   (silent fallback, never raises)
```

- Runs the sync work in `asyncio.to_thread` — never blocks the event loop
- Parses `ticker["last_price"]` for `market` entries matching `B-{COIN}_*` pattern
- A single call covers all coins for both bots in one batch
- Never raises; returns `{coin: None}` on any error

---

### `pull_state_payload()` — `app.py`

Changed from two sequential `asyncio.to_thread` calls to one `asyncio.gather`:

```python
_pmb_all, _mtb_all = await asyncio.gather(
    asyncio.to_thread(_pmb_st.load_trades),
    asyncio.to_thread(_mtb_st.load_trades),
)
```

Then collects unique coins across both bots, calls `_get_current_prices_safe` once, and passes the prices dict to both `_enrich_closed_trades` calls.

---

## Frontend Changes

### PMB Trade History table

| Before (9 cols) | After (11 cols) |
|---|---|
| Coin | Coin |
| Action | Action |
| Entry Price | Entry Price |
| Exit Price | Exit Price |
| *(new)* | **Cur. Price** |
| Amount | Amount |
| PnL ₹ | PnL ₹ |
| PnL % | PnL % |
| Holding | Holding |
| *(new)* | **Exit Reason** |
| Timestamp | Timestamp |

### MTB Closed Trades table

| Before (9 cols) | After (10 cols) |
|---|---|
| Symbol | Symbol |
| Entry Price | Entry Price |
| Exit Price | Exit Price |
| *(new)* | **Cur. Price** |
| Proceeds | Proceeds |
| PnL ₹ | PnL ₹ |
| Return % | Return % |
| Holding | Holding |
| Reason (raw) | **Exit Reason** (normalised, coloured) |
| Timestamp | Timestamp |

### Exit Reason badge colours

| Value | Colour |
|---|---|
| `TAKE_PROFIT` | 🟢 Green |
| `STOP_LOSS` | 🔴 Red |
| `TRAILING_STOP` | 🟡 Amber |
| `MANUAL` | 🔵 Blue |
| `UNKNOWN` | Default (no modifier) |

### Current Price rendering

- Displayed as monospace `—` when `current_price` is `None`
- Formatted to 4 decimal places when present
- Uses `text-muted font-mono` classes so it's visually distinct from Exit Price

---

## Regression Checklist

| Check | Result |
|---|---|
| App starts cleanly | ✅ |
| Scanner bootstraps normally | ✅ |
| All bots start normally | ✅ |
| PMB table still renders when no closed trades | ✅ (`colspan="11"` empty row) |
| MTB table still renders when no closed trades | ✅ (`colspan="10"` empty row) |
| Old enrichment fields (`pnl_pct`, `holding_time`, `entry_price`) still present | ✅ |
| `_enrich_closed_trades()` called without `prices` still works | ✅ (defaults to `None`) |
| No scanner logic changes | ✅ |
| No trading engine changes | ✅ |
| No storage schema changes | ✅ |

---

## Rollback Instructions

To revert entirely:

1. **`app.py`** — revert `_enrich_closed_trades` signature (remove `prices` param, remove `exit_reason`/`current_price` blocks), remove `_get_current_prices_safe`, revert the `pull_state_payload` gather back to two `asyncio.to_thread` calls
2. **`dashboard/templates/dashboard.html`** — revert PMB table to 9-col headers/rows; revert MTB table to 9-col headers/rows (restore `pos.reason` in the Reason column)
3. **`dashboard/static/style.css`** — remove the four `.row-badge-pill.badge-*` lines

No JSON file changes, no database migrations, no API contract breaks.
