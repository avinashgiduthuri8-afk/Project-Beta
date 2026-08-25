# PMB Open Positions — Live Current Price / Live PnL

## Feature

The "PMB Open Positions" table now shows three additional columns for each open position:

- **Current Price** — the coin's latest price, read from the scanner's in-memory ticker cache.
- **Live PnL ₹** — unrealized profit/loss in rupees, as a colored badge (green = profit, red = loss, blue = flat).
- **Live PnL %** — unrealized profit/loss as a percentage move from the average entry price, same badge coloring.

Both values update automatically on every dashboard refresh (page load / `/api/v1/state` poll) — they are computed fresh each time, not stored. When a coin's price cannot be found in the ticker cache, all three cells render `—` instead of a guessed or stale value.

**Constraint honored:** zero CoinDCX (or any other) API calls are made for this feature. Prices come exclusively from `_SCANNER._ticker_cache`, which the scanner bot already refreshes on its own schedule for unrelated purposes. If that cache happens to be empty or stale, the new columns simply show `—` rather than reaching out to an exchange.

## Files Changed

- **`app.py`**
  - Added `_TICKER_QUOTE_SUFFIXES` / `_base_coin_from_market()` — extracts a base coin symbol (e.g. `LINK`) from a raw CoinDCX ticker `market` string (e.g. `LINKINR`, `LINKUSDT`, `LINKBTC`). CoinDCX market strings have no separator, so this strips a known quote-currency suffix rather than splitting on `_`.
  - Added `_read_scanner_ticker_cache()` — reads `_SCANNER._ticker_cache` only (no network calls) and returns `{COIN: last_price}`, preferring INR-quoted prices over USDT/BTC/ETH when a coin has multiple listed pairs.
  - Added `_get_scanner_ticker_prices_only(coins)` — async wrapper around the above, run via `asyncio.to_thread` so it never blocks the event loop; returns `None` for any coin missing from the cache.
  - Added `_enrich_open_positions_live_pnl(positions, prices)` — takes the raw `open_positions` list from `pmb_snapshot()` and returns a new list where each position dict has `current_price`, `live_pnl`, `live_pnl_pct`, and `live_pnl_status` appended. It does not mutate or drop any existing field, and returns `None`/`"unavailable"` for positions whose coin has no cached price.
  - In `pull_state_payload()`, added a small enrichment block (wrapped in try/except, best-effort) immediately after the existing closed-trade enrichment: it collects the coins from `pmb_state["open_positions"]`, looks up their prices via `_get_scanner_ticker_prices_only()`, and replaces `pmb_state["open_positions"]` with the enriched list before it is exposed as `pmb_overview`.

- **`dashboard/templates/dashboard.html`**
  - "PMB Open Positions" table: inserted **Current Price**, **Live PnL ₹**, **Live PnL %** columns between "Avg Entry" and "Dips". Live PnL cells render as colored `row-badge-pill` badges (`badge-green` / `badge-red` / `badge-blue`) driven by `pos.live_pnl_status`, matching the badge pattern already used elsewhere in the dashboard (e.g. Trade History result badges). Missing values render as `—`. Updated the "No open PMB positions" empty-state `colspan` from `9` to `12` to match the new column count.

Nothing else was touched. In particular:
- `bots/pmb_bot/trading_engine.py` — **not modified**.
- `bots/pmb_bot/storage.py` — **not modified**.
- Scanner bot logic (`bots/scanner_bot/`) — **not modified**; only read `_SCANNER._ticker_cache` from `app.py`, which is the same read-only access pattern already used by the existing `/api/v1/prices` endpoint.
- PMB Trade History / closed-trade code — **not modified** (verified no regression, see below).

## Formulas

For a position with `avg_entry_price`, `total_quantity` (qty held), `total_invested`, and a looked-up `current_price`:

```
current_value  = current_price × total_quantity
live_pnl        = current_value − total_invested
live_pnl_pct    = ((current_price − avg_entry_price) / avg_entry_price) × 100
```

`live_pnl_status` is derived from the sign of `live_pnl`: `"profit"` (> 0, green), `"loss"` (< 0, red), `"flat"` (== 0, blue). If `current_price` is unavailable (coin missing from the ticker cache) or `avg_entry_price` is not a positive number, all three values are `None` and `live_pnl_status` is `"unavailable"` — rendered as `—` with neutral styling, never a guessed number.

## Bug Found and Fixed Along the Way

While wiring this up, testing against the live app showed `current_price` always came back `null` even though the ticker cache was populated. Root cause: CoinDCX ticker `market` strings have no separator (e.g. `LINKINR`, `LINKUSDT`, `LINKBTC`), but the existing helper pattern this feature was told to reuse (the one behind `/api/v1/prices`) assumed a `B-COIN_QUOTE` format and split on `_`/`B-`, which is a no-op on real data — it never actually strips the quote currency. `_base_coin_from_market()` fixes this (for the new PMB code path only) by stripping a known quote suffix (`INR`, `USDT`, `BTC`, `ETH`, in that priority order) instead of splitting on characters that don't appear in the real data. The pre-existing `/api/v1/prices` endpoint and `_get_current_prices_safe()` were left untouched, since fixing them was out of scope for this task.

## Regression Checks

- Restarted the app (`Start application` workflow); startup log shows no errors across scanner, PMB, MTB, VGX, and risk-engine subsystems.
- Authenticated against `/api/v1/state` with `X-API-Key` and inspected the live PMB `open_positions` payload directly:
  - Confirmed all pre-existing fields (`avg_entry_price`, `total_quantity`, `total_invested`, `dip_count`, `partial_sell_count`, `next_dip_price`, `next_sell_price`, `stop_loss_price`, `id`, `status`, etc.) are present and unchanged.
  - Confirmed the new fields (`current_price`, `live_pnl`, `live_pnl_pct`, `live_pnl_status`) are appended and numerically correct for a real open position (`LINK`, `avg_entry_price=781.66`, `total_quantity=1.2793...`, `total_invested=1000.0`, cached price `782.74` → `live_pnl=1.38`, `live_pnl_pct=0.14`, `status="profit"`).
  - Confirmed `pmb_overview.closed_trades` count and shape are unaffected (still `0` entries in this run, same shape as before the change).
  - Confirmed `vgx_overview.open_positions` and `mtb_overview.open_positions` are unaffected (unrelated bots, no shared code path touched).
- Verified `app.py` parses cleanly (`ast.parse`) after all edits.
- Manually traced the Jinja template changes: the new `<td>` cells use `is not none` checks so `0` values (e.g. exactly-flat PnL) render correctly instead of being treated as falsy/missing; only a genuine `None` renders `—`.

## Screenshot Description

*(Automated screenshot capture of the authenticated dashboard was not available in this session — the preview tool could not establish the cookie-session login required by the dashboard's auth flow. Verification was instead performed directly against the authenticated `/api/v1/state` JSON API, shown above.)*

Expected visual result on the "PMB" tab, "PMB Open Positions" table: the column order is now `Coin | Avg Entry | Current Price | Live PnL ₹ | Live PnL % | Dips | Partial Sells | Invested | Qty Held | Next Dip | Next Sell | Stop Loss`. For the live `LINK` position, "Current Price" shows `782.7400`, "Live PnL ₹" shows a green pill reading `₹1.38`, and "Live PnL %" shows a green pill reading `0.14%` — consistent with the coin trading slightly above its average entry price. If the ticker cache had no price for a coin, those three cells would instead show a plain `—` with muted/neutral text color.
