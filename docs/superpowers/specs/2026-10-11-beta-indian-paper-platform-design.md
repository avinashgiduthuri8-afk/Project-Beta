# Project Beta Indian Paper-Trading Platform Design

**Date:** 2026-10-11  
**Status:** Approved  
**Target release:** Beta v1

## Objective

Project Beta v1 is a local-first, Indian-stock paper-trading platform cloned from Project Alpha's architecture. It preserves Alpha's active three-service operating model and ten-stage learning workflow while replacing cryptocurrency and CoinDCX assumptions with NSE/BSE stock behavior.

Beta v1 must run without real-money execution. It must not reuse credentials, databases, tokens, runtime state, or generated files from Project Alpha. Project Alpha must remain unchanged.

## Release boundary

Beta v1 includes stock scanning, simulated trading, post-trade AI-assisted learning, and Dhan-compatible stock identities and real-time market data. It excludes all broker order placement, live trading, cryptocurrency, derivatives, funds, and VPS deployment.

Real Dhan order execution is deferred to a separately designed Beta v2. No dormant or indirectly reachable live-order path may remain in the v1 runtime.

## Supported market and instruments

- NSE and BSE listed cash equities only.
- Canonical symbols use `EXCHANGE:TRADINGSYMBOL`, for example `NSE:RELIANCE` and `BSE:500325`.
- Dhan `securityId` is stored as the provider identifier when available.
- Quantities are positive whole shares.
- Prices are normalized to the instrument tick size.
- The default portfolio currency is INR.
- No cryptocurrency, ETFs, mutual funds, futures, options, forex, or CFDs.

## Active service order

The active fleet follows Project Alpha's operational order:

1. **Scanner Server**
2. **Paper Trading Server**
3. **AI Intelligence / Learner Server**

The AI Learner runs after paper trading. It analyzes completed paper trades and validates parameter adjustments; it is not a pre-trade LLM approval service and cannot place orders.

### Scanner Server

The Scanner owns market-data ingestion, data-quality checks, stock-universe validation, multi-timeframe calculations, confluence scoring, strategy evaluation, signal ranking, expiry, cooldown, and deduplication. It writes only to `scanner/data/beta_scanner.db`.

### Paper Trading Server

The Paper service reads qualified signals from the Scanner database through a read-only connection. It owns simulated orders, cash allocation, risk controls, positions, exits, Indian charge estimates, portfolio accounting, and the immutable trade journal. It writes only to `paper/data/beta_paper.db`.

### AI Intelligence / Learner Server

The Learner reads completed paper trades and scanner evidence through read-only connections. It owns post-trade diagnosis, bounded parameter proposals, historical validation, paper canaries, promotion, and rollback. It writes only to `learner/data/beta_learner.db`.

Services must not import one another or depend on a cross-process in-memory event bus. Reusable contracts, repositories, market types, and pure calculations belong under `shared/`.

## Ten-stage workflow

Every opportunity and resulting learning feedback follows these stages in order:

### Scanner stages

1. **Market-data ingestion:** Load normalized NSE/BSE candles and quotes from the configured provider.
2. **Market-quality gates:** Reject stale, malformed, illiquid, insufficient-volume, or ineligible stocks.
3. **Market analysis:** Calculate multi-timeframe trend, volatility, indicators, and confluence using stock-market sessions.
4. **Strategy evaluation:** Run supported stock strategies, score candidates, and rank opportunities.
5. **Signal qualification:** Apply expiry, cooldown, duplicate prevention, and persistence before exposing a paper signal.

### Paper stages

6. **Portfolio and risk decision:** Enforce paper cash, per-position exposure, portfolio exposure, open-position, daily-loss, and emergency-stop limits.
7. **Paper lifecycle:** Simulate entries and exits, maintain positions, estimate Indian charges, calculate realized/unrealized P&L, and write the trade journal.

### Learner stages

8. **Post-trade diagnosis:** Validate completed journal records and identify stable, deduplicated mistake patterns.
9. **Walk-forward validation:** Test bounded parameter proposals on historical scanner evidence with no look-ahead and reject insufficient or degrading candidates.
10. **Paper canary and rollback:** Promote only candidates that pass measured paper outcomes and automatically roll back failed calibrations.

No signal or calibration may silently bypass a stage. Each rejection must record a machine-readable code and an operator-readable reason.

## Market-data providers

### Keyless default

Yahoo's public chart data provides delayed NSE/BSE candles and quotes for research and paper trading without credentials. Requests are rate-limited, retried with bounds, cached, and limited to the configured watchlist. The application clearly labels this data as delayed and non-execution-grade.

### Optional Dhan real-time feed

A single persistent Dhan WebSocket may provide real-time quotes using credentials created only for Project Beta. It supplies market data only in v1. Dhan order placement, modification, cancellation, funds, and holdings endpoints are outside v1 scope.

Dhan's public instrument master supplies stock-to-`securityId` mappings. Provider calls are centralized in the Scanner so Paper and Learner reuse persisted evidence instead of fetching the same data independently.

If the active feed disconnects, becomes stale, or returns an ambiguous instrument mapping, the Scanner pauses affected stocks and records the reason. It must not silently mix stale public data with a nominally real-time stream.

## Paper-trading model

- Long-only simulated cash equity trading.
- No margin, leverage, or short selling.
- Configurable starting cash and per-position allocation.
- Whole-share sizing, stock tick rounding, and exchange-session enforcement.
- Restart-safe signal and position idempotency.
- Market, limit, stop-loss, and take-profit simulation must use explicit fill rules and recorded data timestamps.
- Indian statutory and broker charges are estimates; the model includes applicable STT, stamp duty, exchange and SEBI charges, GST, brokerage, and slippage without calculating personal income tax.

## Learning safety boundary

The Learner may propose and validate only explicitly allowlisted strategy weights and confluence thresholds. It cannot:

- generate or modify source code;
- invent or activate strategies;
- change cash, position, loss, stop-loss, or emergency-stop safety limits;
- enable live trading or broker order APIs;
- promote a candidate without historical validation and paper-canary evidence.

The Scanner reads only promoted calibrations. Missing, corrupt, or incomplete learning evidence fails closed to the last known safe parameters.

## Failure handling and recovery

- Closed market or stale data prevents new entries.
- Provider failure is isolated per symbol where safe; systemic feed failure pauses scanning.
- Database handoffs use persistent statuses and stable identifiers so process restarts cannot duplicate work.
- Paper cash and open positions are reconstructed from persisted state before new signals are accepted.
- Interrupted learner cycles retain a terminal or recoverable status and cannot partially promote changes.
- Logs and operator notifications name the service, stage, symbol, and rejection reason without exposing secrets.

## Security and configuration

- Paper mode is the only v1 execution mode.
- Secrets live only in ignored local environment files.
- Alpha credentials and runtime files are never copied or referenced.
- Dhan credentials are optional and market-data-only in v1.
- Sanitized configuration and logs redact tokens, client IDs where sensitive, Telegram tokens, and optional AI-provider keys.
- No default, sample, test, or placeholder value may enable external order placement.

## Removal and migration scope

The v1 conversion removes or replaces active CoinDCX clients, crypto pair parsing, crypto market assumptions, TDS-specific accounting, crypto news sources, real-money execution routes, live-service deployment assets, and obsolete Alpha branding. It also removes Zerodha and INDstocks integration from the supported runtime.

Generic service isolation, repositories, paper lifecycle, risk controls, strategy code, journal, learner validation, and notification behavior may be retained only after their inputs, names, units, and tests are converted to Indian equities.

## Resource envelope and future deployment

Development and verification occur locally. No VPS connection, upload, provisioning, or deployment is authorized during Beta v1 implementation.

The application must nevertheless fit a future server with:

- 1 vCPU;
- 2 GB RAM;
- 20 GB SSD.

To meet that envelope, the design uses one market-data collector, bounded queues and caches, batched SQLite writes, database retention, compressed/rotated logs, limited worker counts, and configurable watchlist size. Future deployment configuration may be prepared locally, but actual deployment waits until the project code is complete and the user supplies a new server.

## Testing and acceptance

Automated tests cover:

- all ten stages and their rejection paths;
- Scanner, Paper, and Learner import/database isolation;
- NSE/BSE symbols, Dhan security IDs, tick sizes, whole-share sizing, and market sessions;
- Yahoo caching/rate limits and mocked Dhan WebSocket events;
- stale data, disconnects, retry exhaustion, and ambiguous instruments;
- paper fills, exits, cash accounting, Indian charges, and P&L;
- restart recovery, signal idempotency, and duplicate-position prevention;
- learner journal validation, walk-forward checks, canary promotion, and rollback;
- secret redaction and absence of reachable live-order paths.

Completion requires the focused Beta suite and relevant regression suite to pass, Python compilation and static critical-error checks to succeed, secret/runtime-data scans to pass, and Project Alpha's Git state to remain unchanged.

## Delivery sequence

1. Produce and approve a detailed implementation plan.
2. Implement Beta v1 locally in small, test-driven phases.
3. Verify the complete three-service paper fleet within the resource envelope.
4. Commit the completed code and request explicit approval before pushing.
5. Discuss the new cloud server only after the code is complete.
6. Design real Dhan execution separately as Beta v2.
