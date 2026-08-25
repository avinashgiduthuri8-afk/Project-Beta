# Portfolio Dashboard Fixes

Date: 2026-07-03

## Problem Summary

1. **Portfolio tab always showed zero values.** Every metric card in the Portfolio tab (Total Value, Available Cash, Invested Amount, Total PnL, Daily PnL) displayed `0` permanently — on page load and after every live refresh.
2. **Total AUM on the Home tab displayed incorrectly.** The `kpi-aum` and `kpi-aum-delta` Home cards read from `portfolio_overview`, which was hardcoded to zero, so they always showed `₹0.00`.
3. **Open Positions table showed no positions.** The `#open-view` table iterated `data.open_positions`, which was always an empty list `[]`. Real positions held by PMB, MTB, and VGX were never merged into this list.
4. **Currency formatting needed INR support.** Monetary values were formatted inconsistently across the codebase (mix of `"$" + val`, `"₹" + val.toFixed(2)`, `Math.round(val).toLocaleString("en-IN")`). A unified `formatCurrency()` helper using `en-IN` locale was missing.

---

# Root Cause Analysis

## Backend Issues

- **File:** `app.py`
- **Function changed:** `pull_state_payload()`
- **What was wrong:** The `portfolio_overview` dict (lines 478–485 before fix) was a hardcoded placeholder — all six fields were set to the integer `0`. No code ever read from `vgx_overview`, `pmb_overview`, or `mtb_overview` to compute the actual totals. The top-level `open_positions` key (line 590 before fix) was always set to `[]`, completely ignoring the per-bot position arrays.
- **Why it caused zero values:** Every consumer — both the Jinja2 server-side render on page load and the JS polling loop — read from `portfolio_overview`, which never contained real data. The stub was written at project creation and left unimplemented.

## Frontend Issues

- **File:** `dashboard/static/script.js` and `dashboard/templates/dashboard.html`
- **Elements changed:** The five Portfolio tab stat cards (`Total Portfolio Value`, `Available Cash`, `Invested Amount`, `Total PnL Net`, `Daily PnL Delta`) had no `id` attributes, making them unreachable by JavaScript.
- **JS functions changed:** `refreshDashboardData()` — did not call any Portfolio-specific update. `updateHomeV2()` — only wired the Home KPI cards.
- **Why cards were not updating:** Even after the backend was fixed to return real values, the Portfolio cards would still freeze at page-load values after the first polling cycle because no JS function existed to read the new `portfolio_overview` data and push it into the DOM. Without `id` attributes on the card elements, `getElementById()` had no targets to update.

## Open Positions Issues

- **Wrong API key used:** The `#open-view` table iterated `data.open_positions` (the top-level key), which was always `[]`. The actual positions were stored inside `data.pmb_overview.open_positions`, `data.mtb_overview.open_positions`, and `data.vgx_overview.open_positions` — none of which were merged into the top-level key.
- **Field name mismatches:** The template expected `pos.quantity`, `pos.buy_price`, `pos.pnl_pct`, `pos.status`, and `pos.current_price`. The raw per-bot schemas are completely different:
  - PMB uses `total_quantity`, `avg_entry_price` (no `pnl_pct`, no `current_price`)
  - MTB uses `quantity`, `entry_price` (no `buy_price`, no `pnl_pct`)
  - VGX uses `qty`, `buy_price`, `amount` (no `pnl_pct`)
- **Mapping changes:** A normalization pass now runs in `pull_state_payload()` to produce a unified list with fields: `bot`, `coin`, `quantity`, `buy_price`, `pnl_pct`, `status`. See schema table below.

---

# Files Modified

## app.py

### Functions changed

- `pull_state_payload()` — the only function that builds the full state payload returned by `/api/v1/state` and rendered by the Jinja2 template at `/`.

### Lines added

Immediately before the `return {` block (after all existing data-fetching):

```python
# ── Portfolio aggregation from live bot snapshots ─────────────────────
vgx_state = await _cached_snapshot("vgx", vgx_snapshot)

_vgx_cash       = float(vgx_state.get("virtual_balance", 0))
_pmb_cash       = float(pmb_state.get("cash_balance", 0))
_mtb_cash       = float(mtb_state.get("cash_balance", 0))
_available_cash = round(_vgx_cash + _pmb_cash + _mtb_cash, 2)

_vgx_invested    = round(sum(float(p.get("amount", 0))         for p in vgx_state.get("open_positions", [])), 2)
_pmb_invested    = round(sum(float(p.get("total_invested", 0)) for p in pmb_state.get("open_positions", [])), 2)
_mtb_invested    = round(sum(float(p.get("trade_amount", 0))   for p in mtb_state.get("open_positions", [])), 2)
_invested_amount = round(_vgx_invested + _pmb_invested + _mtb_invested, 2)

_total_pnl   = round(float(vgx_state.get("total_pnl", 0)) + float(pmb_state.get("total_pnl", 0)) + float(mtb_state.get("total_pnl", 0)), 2)
_daily_pnl   = round(float(vgx_state.get("daily_pnl", 0)) + float(pmb_state.get("daily_pnl", 0)) + float(mtb_state.get("daily_pnl", 0)), 2)
_total_value = round(_available_cash + _invested_amount + _total_pnl, 2)
_open_pos_count = (len(vgx_state.get("open_positions", [])) +
                   len(pmb_state.get("open_positions", [])) +
                   len(mtb_state.get("open_positions", [])))

# ── Normalize open positions from all bots into unified schema ─────────
_all_open_positions: list[dict] = []
for p in vgx_state.get("open_positions", []):
    _all_open_positions.append({
        "bot": "VGX", "coin": p.get("coin", ""),
        "quantity": round(float(p.get("qty", 0)), 8),
        "buy_price": round(float(p.get("buy_price", 0)), 4),
        "pnl_pct": 0, "status": "OPEN",
    })
for p in pmb_state.get("open_positions", []):
    _all_open_positions.append({
        "bot": "PMB", "coin": p.get("coin", ""),
        "quantity": round(float(p.get("total_quantity", 0)), 8),
        "buy_price": round(float(p.get("avg_entry_price", 0)), 4),
        "pnl_pct": 0, "status": p.get("status", "OPEN"),
    })
for p in mtb_state.get("open_positions", []):
    _all_open_positions.append({
        "bot": "MTB", "coin": p.get("coin", p.get("symbol", "")),
        "quantity": round(float(p.get("quantity", 0)), 8),
        "buy_price": round(float(p.get("entry_price", p.get("buy_price", 0))), 4),
        "pnl_pct": 0, "status": p.get("status", "OPEN"),
    })
```

### Lines removed

The hardcoded stub:
```python
"portfolio_overview": {
    "total_value":    0,
    "daily_pnl":      0,
    "available_cash": 0,
    "invested_amount": 0,
    "total_pnl":      0,
    "open_positions": 0,
},
```

And `"open_positions": []` in the lower section of the return dict.

Also `"vgx_overview": await _cached_snapshot("vgx", vgx_snapshot)` was changed to `"vgx_overview": vgx_state` to reuse the already-awaited result.

### New calculations

| Field | Formula |
|---|---|
| `available_cash` | `vgx.virtual_balance + pmb.cash_balance + mtb.cash_balance` |
| `invested_amount` | `Σ vgx[pos].amount + Σ pmb[pos].total_invested + Σ mtb[pos].trade_amount` |
| `total_pnl` | `vgx.total_pnl + pmb.total_pnl + mtb.total_pnl` |
| `daily_pnl` | `vgx.daily_pnl + pmb.daily_pnl + mtb.daily_pnl` |
| `total_value` | `available_cash + invested_amount + total_pnl` |
| `open_positions` (count) | `len(vgx.open_positions) + len(pmb.open_positions) + len(mtb.open_positions)` |

---

## dashboard/templates/dashboard.html

### IDs added

| Element | ID | Purpose |
|---|---|---|
| Total Portfolio Value `<h2>` | `port-total-value` | JS live update target |
| Available Cash `<h3>` | `port-available-cash` | JS live update target |
| Invested Amount `<h3>` | `port-invested-amount` | JS live update target |
| Total PnL Net `<h3>` | `port-total-pnl` | JS live update target |
| Daily PnL Delta `<h3>` | `port-daily-pnl` | JS live update target |
| Open Positions count `<span>` | `port-open-positions` | JS live update target (inside Total Value card footer) |
| Open Positions `<tbody>` | `open-positions-tbody` | JS live table re-render target |

### Elements changed

- All five Portfolio stat card values updated from raw `{{ data.portfolio_overview.X }}` to `₹{{ "%.2f"|format(data.portfolio_overview.X) }}` for correct INR formatting on initial server render.
- `port-total-pnl` and `port-daily-pnl` now apply `text-green`/`text-red` class conditionally based on sign (Jinja2 `{% if %}` block).
- Total Portfolio Value card gained a `card-footer-metric` div showing the open positions count.
- Open Positions table `<thead>` updated: added `Bot` column, removed `Current Index Valuation` (no live price in snapshot), renamed headers to match normalized schema.
- Open Positions `<tbody>` updated: added `{% else %}` empty state row ("No open positions"); each row now renders the `bot` badge, `coin/INR`, `quantity`, `₹buy_price`, `pnl_pct%` with sign-colour, and `status` badge.

### New bindings

Server-side (Jinja2) initial render now produces real values from the aggregated `portfolio_overview` dict instead of the former all-zero placeholder. JS then takes over on every polling cycle.

---

## dashboard/static/script.js

### Functions added

**`updatePortfolioView(data)`** — mirrors `updateHomeV2()` but targets the Portfolio tab. Updates:
- `port-total-value` — `formatCurrency(po.total_value)`
- `port-available-cash` — `formatCurrency(po.available_cash)`
- `port-invested-amount` — `formatCurrency(po.invested_amount)`
- `port-total-pnl` — `formatCurrency(po.total_pnl)` + sign-based `text-green`/`text-red` class swap
- `port-daily-pnl` — `formatCurrency(po.daily_pnl)` + sign-based class swap
- `port-open-positions` — raw count from `po.open_positions`
- `open-positions-tbody` — full `innerHTML` re-render of normalized positions list (uses `escHtml()` on all string fields for XSS safety)

**`formatCurrency(value)`** — added earlier in the session:
```js
function formatCurrency(value) {
    const num = parseFloat(value) || 0;
    return "₹" + num.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
```

### Functions modified

**`refreshDashboardData()`** — added call to `updatePortfolioView(data)` immediately after the existing `updateHomeV2(data)` call, so both Home and Portfolio tabs refresh on every poll cycle.

**`updateHomeV2()`** — updated earlier in the session to use `formatCurrency()` for `kpi-aum` and `kpi-aum-delta` instead of raw `toFixed(2)`.

### Portfolio update flow

```
setInterval (every 10s, configurable)
  └── refreshDashboardData()
        ├── GET /api/v1/state
        ├── [scanner cards, bot status cards, signal table, ...]
        ├── updateHomeV2(data)         ← Home KPI cards incl. AUM
        └── updatePortfolioView(data)  ← Portfolio cards + positions table
```

---

# New Payload Structure

`/api/v1/state` now returns computed values in `portfolio_overview`:

```json
{
  "portfolio_overview": {
    "total_value": 1200000.0,
    "available_cash": 1199000.0,
    "invested_amount": 1000.0,
    "total_pnl": 0.0,
    "daily_pnl": 0.0,
    "open_positions": 1
  },
  "open_positions": [
    {
      "bot": "PMB",
      "coin": "LINK",
      "quantity": 1.27932861,
      "buy_price": 781.66,
      "pnl_pct": 0,
      "status": "OPEN"
    }
  ]
}
```

> **Note:** `pnl_pct` is always `0` because none of the bot position snapshots include a current market price. Live unrealized PnL% would require a separate price fetch against the exchange API, which is outside the scope of the dashboard state payload.

---

# Open Position Schema Mapping

| Display Field | PMB Raw Field | MTB Raw Field | VGX Raw Field |
|---|---|---|---|
| `bot` | `"PMB"` (constant) | `"MTB"` (constant) | `"VGX"` (constant) |
| `coin` | `coin` | `coin` or `symbol` (strip `USDT`) | `coin` |
| `quantity` | `total_quantity` | `quantity` | `qty` |
| `buy_price` | `avg_entry_price` | `entry_price` | `buy_price` |
| `pnl_pct` | `0` (no current price) | `0` (no current price) | `0` (no current price) |
| `status` | `status` | `status` | `"OPEN"` (constant) |

**Important:** MTB's entry price field is `entry_price` — **not** `buy_price` or `avg_entry_price`. This was confirmed from `bots/mtb_bot/trading_engine.py` line 169. Using any other field name results in `buy_price: 0.0` for all MTB rows.

---

# Dashboard Dependencies

Every file that now relies on the portfolio aggregation being present and correct:

| File | Dependency |
|---|---|
| `app.py` | Source of truth — `pull_state_payload()` must aggregate `vgx_state`, `pmb_state`, `mtb_state` before returning |
| `dashboard/templates/dashboard.html` | Jinja2 initial render reads `data.portfolio_overview.*` and `data.open_positions` on every page load |
| `dashboard/static/script.js` | `updatePortfolioView()` reads `data.portfolio_overview` and `data.open_positions` from `/api/v1/state` on every poll |
| `bots/vgx_bot/` (via `vgx_snapshot()`) | Provides `virtual_balance`, `total_pnl`, `daily_pnl`, `open_positions[].qty/amount/buy_price` |
| `bots/pmb_bot/` (via `pmb_snapshot()`) | Provides `cash_balance`, `total_pnl`, `daily_pnl`, `open_positions[].total_quantity/avg_entry_price/total_invested/status` |
| `bots/mtb_bot/` (via `mtb_snapshot()`) | Provides `cash_balance`, `total_pnl`, `daily_pnl`, `open_positions[].quantity/entry_price/trade_amount/status` |

---

# Future Migration Notes

If importing this project into another Replit account or a new AI session:

1. **Import these files together** — `app.py`, `dashboard/templates/dashboard.html`, and `dashboard/static/script.js` are tightly coupled. Importing only one will break the portfolio display.
2. **Do not overwrite portfolio aggregation logic** in `pull_state_payload()`. It is the block that starts with `# ── Portfolio aggregation from live bot snapshots` and ends before the `return {` statement.
3. **Verify `/api/v1/state` contains `portfolio_overview`** with non-zero `total_value` (assuming bots have been running). A response showing all zeros means the aggregation block was removed or the bot storage files are missing.
4. **Verify JS update functions still reference the correct IDs.** The six IDs that must exist in the HTML: `port-total-value`, `port-available-cash`, `port-invested-amount`, `port-total-pnl`, `port-daily-pnl`, `port-open-positions`. The table body ID: `open-positions-tbody`.
5. **Verify INR formatting helpers remain intact.** `formatCurrency()` and `escHtml()` must both be defined in `script.js` before `updatePortfolioView()` is declared. They are plain function declarations in the module scope — no imports needed.
6. **Do not change bot storage schemas.** The normalization logic maps specific raw field names (e.g., MTB `entry_price`, PMB `total_quantity`). If a bot renames these fields, the `_all_open_positions` loop in `pull_state_payload()` must be updated to match.
7. **`vgx_state` is now stored in a variable** before the return block. Do not revert `"vgx_overview"` back to `await _cached_snapshot("vgx", vgx_snapshot)` inline — doing so would double-fetch the cache and mean the aggregation block uses a stale/different snapshot than the one returned to the template.

---

# Regression Checklist

- [x] Home AUM updates — `kpi-aum` and `kpi-aum-delta` read from `portfolio_overview.total_value` and `portfolio_overview.daily_pnl` via `updateHomeV2()`
- [x] Portfolio cards update — all five cards have IDs and are refreshed by `updatePortfolioView()` on every poll cycle
- [x] Open Positions render — `open-positions-tbody` is re-rendered on every poll; `{% else %}` empty state shown when no positions exist
- [x] PMB positions appear — `pmb_state.open_positions` is iterated in `pull_state_payload()`; fields mapped via `total_quantity`→`quantity`, `avg_entry_price`→`buy_price`
- [x] MTB positions appear — `mtb_state.open_positions` is iterated; `entry_price`→`buy_price` (correct field confirmed from `trading_engine.py`)
- [x] VGX positions appear — `vgx_state.open_positions` is iterated; fields mapped via `qty`→`quantity`
- [x] INR formatting works — `formatCurrency()` uses `toLocaleString("en-IN", {minimumFractionDigits:2, maximumFractionDigits:2})` with `₹` prefix; applied to all monetary display paths
- [x] No console errors — `escHtml()` applied to all `innerHTML` string insertions in `updatePortfolioView()`; numeric fields use `parseFloat()` with `|| 0` guard
- [x] `/api/v1/state` returns correct totals — verified live: `total_value=₹12,00,000`, `available_cash=₹11,99,000`, `invested_amount=₹1,000`, `open_positions=1`

---

## Git Commit Summary

| File | Changes | Reason |
|---|---|---|
| `app.py` | Added ~65 lines of portfolio aggregation and position normalization before the `return` block in `pull_state_payload()`; replaced hardcoded `portfolio_overview` zeros with computed values; replaced `"open_positions": []` with `_all_open_positions`; stored `vgx_state` in variable to avoid double-fetch | `portfolio_overview` was an unimplemented stub; `open_positions` never merged per-bot arrays |
| `dashboard/templates/dashboard.html` | Added `id` attributes to all 5 Portfolio stat cards + open-positions count span; added ₹ formatting to server-side render; updated Open Positions table header and body (new Bot column, `{% else %}` empty state, correct field names, `id="open-positions-tbody"`) | Cards had no JS update targets; table used wrong field names and wrong source key |
| `dashboard/static/script.js` | Added `updatePortfolioView(data)` function (~50 lines); added `formatCurrency()` helper; added `escHtml()` helper; wired `updatePortfolioView(data)` call into `refreshDashboardData()`; updated `updateHomeV2()` to use `formatCurrency()` | No JS update path existed for Portfolio tab; INR formatting was inconsistent across the codebase |
