# PMB/MTB Trade History Enhancement

**Status:** Complete  
**Date:** 2026-07-04

---

## Problem

The trade history tables for PMB and MTB showed only the raw stored fields.  
Missing from the UI:

| Field | PMB | MTB |
|---|---|---|
| Entry Price | ❌ not shown | ❌ not shown |
| PnL % | ❌ not computed | ✅ stored as `return_pct` |
| Holding Time | ❌ | ❌ |

---

## Approach

All enrichment is **computed dynamically in `app.py`** at render time.  
No storage schema changes were made.

### How entry_price and holding_time are derived

Every trade record (buy and sell) for both bots shares a common position `id` (e.g., `MTB-BTCUSDT-1234567890` / `PMB-BTC-1234567890`).  Buy records carry the entry `price` and `timestamp`.  Sell/close records carry the exit `price` and `timestamp`.

By loading the **full trades list** (not just the snapshot slice) and building an `id → earliest BUY trade` index, each closed/sell record can be joined to its opening record to produce:

- **`entry_price`** — the buy `price` from the matching BUY record  
- **`holding_time`** — exit `timestamp` − buy `timestamp`, formatted as `1h 23m`, `45m 12s`, etc.

### How pnl_pct is derived

For PMB: `cost = proceeds − pnl` (since `pnl = proceeds − cost`), so  
`pnl_pct = pnl / cost × 100`.

For MTB: `return_pct` is already stored precisely by `close_position()`, so it is preferred over recomputing.

---

## New Functions Added to `app.py`

```python
def _compute_holding_time(entry_ts: str, exit_ts: str) -> str:
    """Return human-readable duration between two ISO-8601 timestamps."""

def _enrich_closed_trades(trades: list[dict], all_trades: list[dict]) -> list[dict]:
    """Add pnl_pct, holding_time, entry_price to closed trade records."""
```

Both are pure functions that do not write to any storage.

### Enrichment injection (in `pull_state_payload`)

```python
_pmb_all = await asyncio.to_thread(_pmb_st.load_trades)
_mtb_all = await asyncio.to_thread(_mtb_st.load_trades)
pmb_state = {**pmb_state,
             "closed_trades": _enrich_closed_trades(pmb_state["closed_trades"], _pmb_all)}
mtb_state = {**mtb_state,
             "closed_trades": _enrich_closed_trades(mtb_state["closed_trades"], _mtb_all)}
```

The enrichment is **best-effort** — if it raises an exception, the original raw data is served unchanged (the `try/except` in the caller ensures this).

---

## Dashboard Template Changes

### PMB Trade History (`dashboard.html`)

**Before:** `Coin | Action | Price | Amount | PnL | Timestamp` (6 columns)

**After:** `Coin | Action | Entry Price | Exit Price | Amount | PnL ₹ | PnL % | Holding | Timestamp` (9 columns)

New columns:
- **Entry Price** — from matching BUY trade; shows `—` if no match found  
- **PnL %** — computed pnl_pct; shows `—` for buy-side records (pnl = 0)  
- **Holding** — human-readable duration; shows `—` if timestamps unavailable

### MTB Closed Trades (`dashboard.html`)

**Before:** `Symbol | Exit Price | Proceeds | PnL | Return % | Reason | Timestamp` (7 columns)

**After:** `Symbol | Entry Price | Exit Price | Proceeds | PnL ₹ | Return % | Holding | Reason | Timestamp` (9 columns)

New columns:
- **Entry Price** — from matching BUY trade; shows `—` if no match found  
- **Holding** — human-readable duration

---

## Fallback Behaviour

| Scenario | Outcome |
|---|---|
| No matching BUY trade found (old pre-enhancement data) | `entry_price = "—"`, `holding_time = "—"` |
| Enrichment raises any exception | Raw unmodified `closed_trades` rendered (try/except) |
| Trade has no `pnl` field (buy record) | `pnl_pct = 0.0`, shown as `—` in template |

---

## Verification

- Full test suite: **542 passed, 0 failures** (no regressions)  
- App restarts cleanly; dashboard template renders without errors  
- For paper-trading with no closed trades, tables show "No PMB/MTB trade history" correctly
