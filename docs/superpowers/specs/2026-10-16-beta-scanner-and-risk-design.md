# Project Beta Scanner and Risk Design

**Date:** 2026-10-16  
**Status:** Draft for user review  
**Scope:** Scanner-first enhancement with shared storage and paper-risk controls

## Objective

Build a paper-only Indian-equity scanner that searches affordable stocks, including sub-rupee penny stocks, while rejecting illiquid or unsafe candidates. Scanner, Paper, and Learner share one database so every signal, simulated trade, and learning outcome has a single traceable history.

The first eligibility rule is `0 < last_price <= INR 250`. A paper order may allocate at most INR 1,000 including estimated buy-side charges. Total daily paper loss may not exceed INR 1,000.

## Scope and safety boundary

- Project Beta only; Project Alpha remains strictly read-only.
- NSE cash equities are the initial scanner universe. BSE support remains part of Beta but is not added to the first scanner rollout.
- Paper trading only. No live-order placement, broker-order integration, deployment, or remote upload is part of this work.
- Currency is INR, positions are long-only, and quantities are positive whole shares.
- Public/keyless market data remains the default. Optional Dhan credentials are market-data-only.
- The Scanner recommends candidates, Paper exclusively enforces capital and risk, and Learner analyzes completed paper outcomes.

## Shared database architecture

Scanner, Paper, and Learner use one physical SQLite database, initially configured as `data/project_beta.db`. Sharing the file does not give each service unrestricted ownership.

### Table ownership

- Scanner owns market-universe, quote, candle-evidence, rejection, ranking, and signal tables.
- Paper owns account, consumed-signal, order, position, daily-risk, fill, and journal tables.
- Learner owns diagnosis, experiment, validation, calibration, promotion, and rollback tables.
- Each service may read the other services' published tables but may write only its own tables.
- Cross-service references use stable identifiers and foreign keys; services do not import one another.

### Concurrency and recovery

- Enable SQLite WAL mode, foreign keys, a bounded busy timeout, and short transactions.
- Use idempotency keys for signal creation, signal consumption, fills, journal completion, and learning cycles.
- Schema migrations are versioned and run once before services start.
- Every state transition is transactional. A restart must not duplicate a signal, cash debit, position, exit, or learning result.
- Maintain database backups before migrations and use integrity checks during startup and recovery.

## Scanner pipeline

The scanner applies cheap, deterministic filters before expensive indicator and strategy work.

### Stage 1: Universe refresh

- Load active NSE cash-equity instruments from the current instrument master.
- Include the normal equity series used for delivery/cash trading.
- Exclude suspended, delisted, non-tradable, SME, ETF, mutual-fund, preference-share, debt, rights, warrant, futures, and options instruments.
- Exclude instruments subject to restrictions that make normal paper entry or exit unrealistic.
- Refresh the universe daily and preserve the source timestamp and eligibility reason.

### Stage 2: Price and affordability

- Reject missing, stale, zero, negative, NaN, or infinite prices.
- Accept only `0 < last_price <= INR 250`.
- Stocks below INR 1 are allowed and are tagged `PENNY_STOCK` for stricter checks.
- Estimate buy-side charges and compute:

  `max_quantity = floor((INR 1,000 - estimated_buy_charges) / last_price)`

- Reject a stock when the resulting quantity is less than one share.
- The INR 1,000 amount is an all-in ceiling, not merely the gross share value.

### Stage 3: Data quality and session checks

- Scan only during configured NSE market sessions and use an exchange holiday calendar.
- Reject quotes older than the configured freshness threshold.
- Validate ordered timestamps, positive OHLC values, non-negative volume, OHLC geometry, duplicate timestamps, gaps, and abnormal jumps.
- Adjust historical analysis for splits, bonuses, and other corporate actions.
- Require enough continuous history for every indicator or strategy being evaluated; never use a shortened fallback that changes the strategy definition silently.

### Stage 4: Liquidity and penny-stock safety

- Use 20 completed sessions for normal liquidity evaluation.
- Standard stocks at or above INR 1 require median daily traded value of at least INR 50 lakh.
- Penny stocks below INR 1 require median daily traded value of at least INR 1 crore.
- Standard stocks require a quoted or estimated spread no greater than 1.5%.
- Penny stocks require a quoted or estimated spread no greater than 1.0%.
- Reject persistent zero-volume intervals, discontinuous trading, and candidates repeatedly locked at upper or lower circuits.
- Reject a candidate when required liquidity or spread evidence is unavailable. Data absence is not treated as safety.
- Keep price-band, surveillance, and circuit rejections as machine-readable audit reasons.

### Stage 5: Broad ranking

Rank all survivors using deterministic inputs:

- liquidity and spread quality;
- price and volume momentum;
- relative strength versus NIFTY 50 and the stock's sector index;
- daily and weekly trend alignment;
- volatility suitability;
- market and sector regime;
- data completeness and freshness.

Only the top 50 candidates proceed to deep strategy evaluation. Ranking ties use canonical symbol order so repeated runs over identical evidence produce identical results.

### Stage 6: Deep strategy evaluation

- Evaluate the existing STE, HDA, VCP, BBS, and NR7 strategies.
- Preserve the current minimum strategy score of 85 until validated evidence approves a different value.
- Require the strategy's full history window and exact indicator inputs.
- Record each strategy result, evidence timestamp, parameters, score components, and rejection reason.
- Multi-strategy agreement may increase rank but must not manufacture a probability claim.

### Stage 7: News and risk context

- Reject confirmed high-risk negative events under the configured news policy.
- When news is unavailable, record `NEWS_UNKNOWN`; do not silently label the stock safe.
- News unavailability may be configured to hold the candidate from final qualification while still retaining its technical analysis for later review.
- Never expose credentials or raw provider secrets in logs or stored evidence.

### Stage 8: Final qualification

- Apply signal expiry, cooldown, deterministic signal ID, and duplicate prevention.
- Recheck price, freshness, spread, and affordability immediately before publishing.
- Publish a ranked shortlist of 5 to 10 qualified stocks when enough candidates exist; publish fewer rather than lowering safety rules.
- Store every accepted and rejected candidate with an operator-readable explanation and machine-readable reason code.

## Paper risk rules

### Per-trade allocation

- Maximum all-in allocation per new position is INR 1,000, including estimated buy-side charges.
- Paper independently recalculates quantity and charges; it never trusts Scanner sizing blindly.
- Reject fractional, zero, negative, stale-price, insufficient-cash, and over-budget orders.
- Recheck limits transactionally immediately before cash debit and position creation.

### Daily loss limit

- The hard daily paper-loss limit is INR 1,000.
- Daily loss is calculated from realized P&L, unrealized mark-to-market P&L, and all estimated charges for the current trading day.
- At INR 750 loss, record a warning and reduce operational risk visibility; this does not change fixed limits.
- At INR 900 loss, block new entries and cancel unfilled entry orders.
- At INR 1,000 loss, activate the emergency stop, cancel pending entries, close remaining paper positions using the defined simulated-fill rules, and block trading until the next valid NSE session.
- Gaps and simulated slippage can make final booked loss slightly greater than INR 1,000; the system must record any overshoot rather than hide it.
- Daily reset uses the Asia/Kolkata trading date and occurs only after account reconstruction and reconciliation.
- Manual restart, process restart, Scanner signal, or Learner proposal cannot clear the same-day emergency stop.

### Additional portfolio controls

- Maximum simultaneous open positions starts at three.
- Maximum new positions from one sector starts at one while the portfolio is this small.
- Stop new entries after three consecutive losing exits in one trading day, even when daily loss is below INR 900.
- Reject a new entry if cash after estimated charges would become negative.
- Persist all risk decisions, limit values, P&L inputs, and timestamps.

These defaults are intentionally conservative and remain configuration values bounded by immutable safety ceilings.

## Learner permissions and restrictions

The Learner may analyze rejection records, completed paper trades, strategy evidence, and market regime. It may propose bounded changes only to allowlisted ranking weights and strategy thresholds after walk-forward and paper-canary validation.

The Learner may not:

- raise the INR 250 price ceiling;
- exclude the `price > 0` validation;
- raise the INR 1,000 all-in trade limit;
- raise or disable the INR 1,000 daily-loss limit;
- weaken penny-stock liquidity, spread, circuit, or tradability protections;
- change cash, enable leverage or short selling, write source code, place orders, or enable live trading;
- promote uncalibrated stock-movement probabilities.

Missing, corrupt, incomplete, or insufficient learning evidence fails closed to the last known safe promoted parameters.

## Observability and failure handling

- Every scanner rejection includes stage, symbol, evidence timestamp, reason code, and readable message.
- Every risk rejection includes account state, requested allocation, estimated charges, daily P&L, and active limit without exposing secrets.
- Provider failure isolated to one symbol rejects that symbol; systemic staleness pauses scanner qualification.
- Database lock exhaustion, failed migration, or failed integrity check prevents dependent services from starting.
- Notifications report service, stage, symbol, and action. Notifications never bypass persisted state.
- No component claims a precise movement probability until out-of-sample calibration demonstrates reliability.

## Initial reason codes

- `INSTRUMENT_INELIGIBLE`
- `INVALID_PRICE`
- `PRICE_ABOVE_LIMIT`
- `STALE_QUOTE`
- `MARKET_CLOSED`
- `INSUFFICIENT_HISTORY`
- `INVALID_CANDLES`
- `INSUFFICIENT_LIQUIDITY`
- `SPREAD_TOO_WIDE`
- `SPREAD_UNKNOWN`
- `CIRCUIT_RISK`
- `SURVEILLANCE_RESTRICTED`
- `NOT_AFFORDABLE`
- `REGIME_REJECTED`
- `STRATEGY_NOT_QUALIFIED`
- `NEWS_HIGH_RISK`
- `NEWS_UNKNOWN`
- `DUPLICATE_SIGNAL`
- `COOLDOWN_ACTIVE`
- `TRADE_ALLOCATION_EXCEEDED`
- `INSUFFICIENT_CASH`
- `DAILY_LOSS_WARNING`
- `NEW_ENTRIES_BLOCKED`
- `DAILY_LOSS_LIMIT_REACHED`
- `CONSECUTIVE_LOSS_LIMIT_REACHED`

## Migration sequence

1. Introduce the shared-database schema and table-ownership contract without deleting existing databases.
2. Migrate and reconcile existing Scanner, Paper, and Learner records into the shared database using stable identifiers and row-count/integrity checks.
3. Switch all three services to the shared path only after migration verification and a recoverable backup.
4. Add the scanner eligibility, penny-stock safety, ranking, shortlist, and audit stages.
5. Add Paper affordability and daily-loss enforcement before accepting newly qualified signals.
6. Restrict Learner proposals with immutable safety ceilings.
7. Run deterministic unit, integration, restart, migration, concurrency, and end-to-end paper tests.
8. Keep all work local and stop before any push, deployment, or live-order work.

## Acceptance criteria

- Scanner, Paper, and Learner use one configured database and obey table ownership.
- Concurrent service operation and restarts do not duplicate or corrupt records.
- A valid stock priced INR 250 qualifies for further checks; a stock above INR 250 does not.
- A valid INR 0.25 stock may proceed only after passing the stricter penny-stock rules.
- Zero, negative, stale, non-finite, suspended, illiquid, wide-spread, or circuit-locked stocks cannot qualify.
- A Paper entry's share value plus estimated buy charges never exceeds INR 1,000.
- New entries stop at INR 900 daily loss and the emergency stop activates at INR 1,000.
- The emergency stop survives process restarts and cannot be changed by Learner.
- The broad scan deep-evaluates no more than 50 candidates and publishes no more than 10 qualified signals per cycle.
- Every decision is traceable from market evidence through signal, paper trade, and learning outcome.
- Tests use temporary databases and mocked data; they make no live orders or uncontrolled network calls.
- Project Alpha remains unchanged, and no Git push or remote deployment occurs.

## Out of scope

- Real-money trading or broker order APIs.
- Options, futures, margin, leverage, short selling, crypto, mutual funds, or ETFs.
- Guaranteed profit or guaranteed stock-price prediction.
- Deployment, VPS changes, Git push, or copying credentials from Project Alpha.
