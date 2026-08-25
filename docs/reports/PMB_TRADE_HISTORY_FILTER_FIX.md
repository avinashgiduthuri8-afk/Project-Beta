# PMB Trade History Filter Fix

## Bug

PMB Trade History showed open, unclosed positions (e.g. `BASE_BUY`) as if they were completed trades.

Example of the incorrect row:

```
LINK | BASE_BUY | Exit Price = Entry Price | Holding = 0s | Exit Reason = UNKNOWN
```

`BASE_BUY` is an **entry** transaction (a position being opened), not a completed trade, so it should never
appear in a "Trade History" table. The presence of a `BASE_BUY` row with `Holding = 0s`, `Exit Reason =
UNKNOWN`, and `Exit Price == Entry Price` was a direct symptom of unfiltered entry rows being rendered as if
they were exits.

## Root Cause

`bots/pmb_bot/storage.py` stores every position event — entries and exits — in a single flat log
(`trades.json`, returned by `load_trades()`). This is the **open trade log**: it contains `BASE_BUY`,
`DIP_BUY_N`, `PARTIAL_SELL_N`, `STOP_LOSS`, etc., each tagged with a `status` of either `OPEN` or `CLOSED`.

`snapshot()["closed_trades"]` — the field the dashboard binds to for "PMB Trade History" — was populated with:

```python
"closed_trades": trades[-50:]
```

i.e. simply the last 50 raw log rows, with **no filtering at all**. Any `BASE_BUY` / `DIP_BUY_N` row that
happened to be in the most recent 50 entries was rendered directly into the Trade History table by
`dashboard/templates/dashboard.html`, which has no defensive filtering of its own — it trusts the data it's
given.

A second instance of the same root cause existed in `bots/pmb_bot/pmb_telegram_bot.py`'s `/stats` command,
which also did `trades[-50:]` instead of filtering by closed status, silently inflating "Breakeven" trade
counts with entry rows that have `pnl == 0`.

## Files Changed

- **`bots/pmb_bot/storage.py`**
  - Added `_ENTRY_ONLY_ACTIONS` / `_CLOSED_EXIT_ACTIONS` constants and a `_is_closed_trade()` helper that
    implements the filtering rule (see below).
  - `get_closed_trades()` now uses `_is_closed_trade()` instead of a bare `status == "CLOSED"` check (which
    would still have let a `DIP_BUY_1` with a mistakenly-set `CLOSED` status through).
  - `snapshot()["closed_trades"]` now returns `get_closed_trades()[-50:]` instead of the raw
    `load_trades()[-50:]`. This is the actual fix — every dashboard consumer of `pmb_state["closed_trades"]`
    (via `app.py`'s `pull_state_payload()` → `_enrich_closed_trades()` → `dashboard.html`) is downstream of
    this function, so filtering here fixes the dashboard, the JSON API responses, and Telegram summaries in
    one place.
  - Added a comment block making explicit that **open trade log (`load_trades()`) != closed trade history
    (`get_closed_trades()`)**, since both read from the same `trades.json` file and it is easy to
    accidentally reach for the wrong one.

- **`bots/pmb_bot/pmb_telegram_bot.py`**
  - `stats_cmd()` now calls `storage.get_closed_trades()[-50:]` instead of `storage.load_trades()[-50:]`, so
    Telegram `/stats` win/loss/breakeven counts are computed only over genuinely completed trades.

No changes were needed in `app.py` — `_enrich_closed_trades()` and the dashboard template already assumed
their input was pre-filtered; they simply had never been given filtered data before.

## Filtering Rules

A trade log record is included in "PMB Trade History" (`get_closed_trades()`) **only if**:

1. Its `action` does **not** start with an entry-only prefix (`BASE_BUY`, `DIP_BUY`) — checked first and
   unconditionally excludes entries regardless of `status`.
2. Its `status == "CLOSED"`.

Concretely:

| Action                       | Status | Included? |
|-------------------------------|--------|-----------|
| `BASE_BUY`                    | any    | ❌ excluded (entry) |
| `DIP_BUY_1`, `DIP_BUY_2`, ...  | any    | ❌ excluded (entry) |
| `PARTIAL_SELL_1`               | `OPEN` | ❌ excluded (position still open) |
| `PARTIAL_SELL_1` / `..._TP`    | `CLOSED` | ✅ included |
| `STOP_LOSS`                    | `CLOSED` | ✅ included |
| `TRAILING_STOP`                 | `CLOSED` | ✅ included |
| `MANUAL_SELL`                   | `CLOSED` | ✅ included |
| `FINAL_SELL`                    | `CLOSED` | ✅ included |

Open positions (any position with `status == "OPEN"`, including partially-filled multi-dip positions still
in progress) remain visible **only** in the "PMB Open Positions" table, sourced from
`storage.get_open_positions()` / `snapshot()["open_positions"]`, which was already correctly scoped and is
unaffected by this change.

## Regression Checks

- Added inline behavioural coverage via `_is_closed_trade()` (unit-testable in isolation): verified against
  `BASE_BUY`/`OPEN`, `DIP_BUY_1`/`OPEN`, `PARTIAL_SELL_1`/`OPEN`, `PARTIAL_SELL_1`/`CLOSED`, `STOP_LOSS`/
  `CLOSED`, `TRAILING_STOP`/`CLOSED`, `MANUAL_SELL`/`CLOSED`, `FINAL_SELL`/`CLOSED`, and
  `PARTIAL_SELL_TP`/`CLOSED` — entries and open-status rows return `False`, closed-exit rows return `True`.
- Restarted the app (`Start application` workflow) after the change and confirmed:
  - No startup errors across scanner, PMB, MTB, VGX, and risk-engine subsystems.
  - Login and dashboard load succeed end-to-end.
- Manual invariant check: after filtering, no row in PMB Trade History can simultaneously show
  `Holding = 0s`, `Exit Reason = UNKNOWN`, and `Exit Price == Entry Price` **unless** the underlying record
  is genuinely `status == "CLOSED"` with a recognized exit `action` — the previous bug's exact symptom
  (a `BASE_BUY` row masquerading as a completed trade) is now structurally impossible, since `BASE_BUY` is
  excluded before the `CLOSED` check ever runs.
- Confirmed `open_positions` derivation (`status == "OPEN"`) was untouched, so currently-open PMB positions
  (e.g. the live `LINK` `BASE_BUY` position in `bots/pmb_bot/data/trades.json`) still surface correctly in
  the "PMB Open Positions" table and no longer leak into "PMB Trade History".
