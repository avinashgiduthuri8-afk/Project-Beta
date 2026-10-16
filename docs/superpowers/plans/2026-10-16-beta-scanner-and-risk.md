# Project Beta Scanner and Risk Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a shared-database, paper-only NSE scanner that accepts prices above INR 0 through INR 250, safely handles penny stocks, limits each trade to INR 1,000 all-in, and stops the paper account at INR 1,000 daily loss.

**Architecture:** Scanner, Paper, and Learner open independent SQLite connections to one database while writing only through service-owned repositories and tables. The Scanner uses a cheap eligibility/ranking pass before deep strategy evaluation; Paper recalculates affordability and enforces persisted portfolio/daily-risk state; Learner can tune only allowlisted strategy values within immutable safety ceilings.

**Tech Stack:** Python 3.12, asyncio, Pydantic Settings, aiosqlite/SQLite WAL, Decimal, pandas indicators, pytest, pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-10-16-beta-scanner-and-risk-design.md`

## Global Constraints

- Project Beta only; Project Alpha is strictly read-only.
- NSE cash equities are the initial scanner universe; BSE remains supported but is not added to this scanner rollout.
- Paper mode only: no live orders, broker-order APIs, VPS work, remote upload, or Git push.
- Use one physical SQLite database at `data/project_beta.db`; each service owns its writes.
- Accept only finite prices satisfying `0 < last_price <= INR 250`.
- Stocks below INR 1 are allowed only through stricter penny-stock gates.
- Maximum all-in allocation per new position is INR 1,000 including estimated buy-side charges.
- Daily loss includes realized P&L, unrealized P&L, and charges; warn at INR 750, block new entries at INR 900, and emergency-stop at INR 1,000.
- Long-only INR cash equities and positive whole-share quantities; no leverage, margin, or short selling.
- Preserve all pre-existing dirty-worktree changes. Do not stage or commit user-owned hunks accidentally.
- Remove credential values from tracked defaults/examples, but never copy, print, or reuse any credential from Project Alpha.

## Review Focus

- A sub-rupee quote such as INR 0.25 must remain eligible for later checks, while zero, negative, NaN, infinity, and INR 250.01 must fail deterministically; Task 3 pins this.
- Three services writing concurrently to one SQLite file must not produce lock leaks, duplicate rows, or disabled foreign keys; Tasks 1 and 2 pin this.
- A restart after INR 1,000 daily loss must preserve the emergency stop and must not accept a new signal after the trading-date reset until reconciliation succeeds; Task 5 pins this.
- Missing bid/ask, holiday, circuit, or liquidity evidence must fail closed instead of being silently treated as safe; Tasks 3 and 4 pin this.
- Learner output containing forbidden risk keys, non-finite values, or values outside allowlisted bounds must be rejected without changing the last safe calibration; Task 6 pins this.

---

### Task 1: Canonical Shared-Database and Safety Configuration

**Files:**
- Modify: `shared/core/config.py`
- Modify: `config/beta.env.example`
- Modify: `services/scanner/config/settings.py`
- Modify: `services/paper_bot/config/settings.py`
- Modify: `services/learner/config.py`
- Modify: `tests/shared/test_canonical_config.py`
- Modify: `tests/beta/test_repository_hygiene.py`

**Interfaces:**
- Produces: `AppConfig.db_path: str` as the canonical shared path with compatibility fields `scanner_db_path`, `paper_db_path`, and `learner_db_path` required to resolve to the same normalized path.
- Produces: exact defaults `order_size_inr=1000`, `price_floor_inr=0`, `price_ceiling_inr=250`, `daily_loss_warning_inr=750`, `daily_entry_block_inr=900`, `daily_loss_limit_inr=1000`, `max_open_positions=3`, `max_positions_per_sector=1`, and `max_consecutive_losses=3`.
- Produces: `AppConfig.validate_v1_safety() -> None` that rejects unequal database paths, invalid threshold ordering, live mode, or enabled trading.

- [ ] **Step 1: Extend configuration tests with exact approved values**

Add tests asserting every database alias resolves to `data/project_beta.db`, the price bounds are `0` and `250`, order size is `1000`, daily thresholds are `750 < 900 < 1000`, and all portfolio caps are positive integers.

- [ ] **Step 2: Add failing configuration safety tests**

Test that unequal Scanner/Paper/Learner paths, order size above INR 1,000, daily limit above INR 1,000, misordered daily thresholds, non-paper mode, and embedded default notification credentials are rejected or absent.

Run: `python -m pytest tests/shared/test_canonical_config.py tests/beta/test_repository_hygiene.py -q`

Expected: FAIL because service paths are separate, order size is INR 1,350, price floor is INR 1, risk thresholds are absent, and tracked notification examples contain values.

- [ ] **Step 3: Implement the canonical configuration contract**

Update `AppConfig` and service-specific settings to use the shared path and exact limits. Keep compatibility aliases only where current imports require them, and validate that all aliases normalize to the same path.

- [ ] **Step 4: Sanitize tracked configuration**

Set Telegram and optional provider credential defaults/examples to blank or `None`; retain variable names and documentation. Record that exposed credentials must be rotated out-of-band without placing replacements in Git.

- [ ] **Step 5: Verify configuration and repository hygiene**

Run: `python -m pytest tests/shared/test_canonical_config.py tests/beta/test_repository_hygiene.py -q`

Expected: PASS with no credential values printed.

- [ ] **Step 6: Create a local checkpoint without capturing unrelated hunks**

Review `git diff --` for every Task 1 file. If its pre-existing hunks cannot be separated safely, leave the task uncommitted and document that fact. Otherwise commit only attributable hunks with `git commit -m "config: define beta scanner risk limits"`; never push.

### Task 2: Shared SQLite Lifecycle, Schema, Ownership, and Migration

**Files:**
- Create: `shared/core/database.py`
- Create: `shared/core/migrations.py`
- Modify: `shared/core/repository/beta_store.py`
- Modify: `shared/core/repository/quote_repo.py`
- Modify: `shared/core/repository/paper_execution_repo.py`
- Create: `tests/beta/test_shared_database.py`
- Modify: `tests/beta/test_stock_domain_and_repositories.py`

**Interfaces:**
- Produces: `open_beta_database(path: str, *, read_only: bool = False) -> aiosqlite.Connection` with row factory, `foreign_keys=ON`, WAL for writable connections, and a bounded busy timeout.
- Produces: `initialize_beta_schema(conn: aiosqlite.Connection) -> None` using a `beta_schema_version` table and idempotent ordered migrations.
- Produces: `migrate_legacy_databases(target_path: Path, legacy_paths: LegacyDatabasePaths, backup_dir: Path) -> MigrationReport` that copies only known tables, preserves stable IDs, verifies row counts and foreign keys, and never deletes sources.
- Produces: repository-owned methods for Scanner signal/audit/ranking reads and writes, Paper state writes, and Learner audit/calibration writes; runtime code does not issue cross-service write SQL.

- [ ] **Step 1: Write failing connection and concurrency tests**

Use one temporary file with three connections. Assert all connections see foreign keys enabled, writable connections use WAL, concurrent idempotent signal/position/audit operations finish within the busy timeout, and reopening preserves committed state.

- [ ] **Step 2: Write failing schema-ownership and migration tests**

Create three legacy temporary databases with overlapping valid IDs, migrate them to one target, and assert backup creation, exact row counts, stable references, duplicate-safe rerun, foreign-key integrity, and unchanged source hashes. Test that service repositories expose only their owned writes.

Run: `python -m pytest tests/beta/test_shared_database.py tests/beta/test_stock_domain_and_repositories.py -q`

Expected: FAIL because shared connection helpers, schema versioning, ownership APIs, and migration do not exist.

- [ ] **Step 3: Implement the connection and schema lifecycle**

Move connection PRAGMAs and schema initialization behind `open_beta_database` and `initialize_beta_schema`. Add service-owned tables for scanner universe/rejections/rankings, paper daily risk, and learner calibrations while retaining existing signal, quote, account, position, journal, and learning data.

- [ ] **Step 4: Implement non-destructive legacy migration**

Back up every existing database before copying. Use one immediate transaction, explicit column mappings, `INSERT OR IGNORE` only for stable duplicate IDs, row-count checks, and `PRAGMA foreign_key_check`; rollback the target on any mismatch and retain every source file.

- [ ] **Step 5: Replace raw persistence calls with repository methods**

Add exact read methods needed by later tasks, including `list_qualified_signals()`, `record_scanner_decision(...)`, `save_rankings(...)`, `load_daily_risk_state(...)`, and `save_calibration_if_safe(...)`. Do not yet rewire service runtime loops.

- [ ] **Step 6: Verify shared persistence**

Run: `python -m pytest tests/beta/test_shared_database.py tests/beta/test_stock_domain_and_repositories.py -q`

Expected: PASS, including a second migration run and three-connection concurrency case.

- [ ] **Step 7: Create a local checkpoint**

After diff attribution, commit only Task 2 changes with `git commit -m "feat: add shared beta database lifecycle"`; never push.

### Task 3: NSE Universe, Market Evidence, and Eligibility Gates

**Files:**
- Create: `shared/market/universe.py`
- Create: `shared/market/holidays.py`
- Modify: `shared/market/instruments.py`
- Modify: `shared/market/models.py`
- Modify: `shared/market/provider.py`
- Modify: `shared/core/repository/quote_repo.py`
- Modify: `shared/market/session.py`
- Create: `services/scanner/eligibility.py`
- Create: `config/nse_holidays.json`
- Create: `tests/beta/test_scanner_eligibility.py`
- Modify: `tests/beta/test_indian_market_and_brokers.py`

**Interfaces:**
- Produces: `UniverseInstrument` with canonical symbol, exchange, series, instrument type, sector, tick size, trading status, SME flag, surveillance status, and price-band/circuit metadata.
- Produces: `NseUniverse.refresh() -> tuple[UniverseInstrument, ...]`, using the public Dhan instrument master without account credentials and accepting only active NSE cash equities allowed by the spec.
- Produces: `MarketQuote.bid: Decimal | None`, `ask: Decimal | None`, and `spread_pct() -> Decimal | None`; providers must use `None` when bid/ask is not genuine.
- Produces: `HolidayCalendar.is_session_day(exchange: str, trading_date: date) -> bool`; missing or stale current-year holiday data prevents qualification.
- Produces: `EligibilityEngine.evaluate(instrument, quote, daily_candles, intraday_candles, now) -> EligibilityDecision` with exact price, freshness, history, liquidity, spread, circuit, and penny-stock rules.

- [ ] **Step 1: Write failing universe and holiday tests**

Feed representative instrument-master rows and assert inclusion of active NSE cash equity and exclusion of BSE for this rollout, SME, suspended, debt, ETF, derivatives, and unknown series. Assert weekend/holiday closure and fail-closed behavior when current-year holiday coverage is absent.

- [ ] **Step 2: Write failing boundary and penny-stock tests**

Test prices `0.25`, `0.99`, `1.00`, and `250.00` can pass the price gate; test `0`, negative, NaN, infinity, and `250.01` produce `INVALID_PRICE` or `PRICE_ABOVE_LIMIT`. Pin standard median turnover to INR 50 lakh and penny median turnover to INR 1 crore.

- [ ] **Step 3: Write failing spread, continuity, and circuit tests**

Assert standard spread above 1.5% and penny spread above 1.0% fail. When genuine quotes are unavailable, use a deterministic 20-bar one-minute range estimator; fewer than 20 valid bars yields `SPREAD_UNKNOWN`. Reject repeated circuits, persistent zero-volume intervals, insufficient 20-session history, and stale evidence.

Run: `python -m pytest tests/beta/test_scanner_eligibility.py tests/beta/test_indian_market_and_brokers.py -q`

Expected: FAIL because universe metadata, holiday coverage, spread evidence, and eligibility engine are absent.

- [ ] **Step 4: Implement normalized universe and quote evidence**

Parse the public instrument master into `UniverseInstrument`, persist the daily universe snapshot, carry genuine bid/ask only when supplied by the provider, and preserve source timestamps. Never represent last price as a synthetic bid and ask.

- [ ] **Step 5: Implement deterministic eligibility gates**

Use `Decimal` for price, turnover, and spread comparisons. Produce one primary reason code plus supporting evidence; never downgrade absent evidence to safe. Tag prices below INR 1 as `PENNY_STOCK` and apply the stricter thresholds.

- [ ] **Step 6: Verify universe and eligibility**

Run: `python -m pytest tests/beta/test_scanner_eligibility.py tests/beta/test_indian_market_and_brokers.py -q`

Expected: PASS without live network access.

- [ ] **Step 7: Create a local checkpoint**

After diff attribution, commit only Task 3 changes with `git commit -m "feat: add NSE scanner eligibility gates"`; never push.

### Task 4: Broad Ranking, Deep Strategy Scan, and Audited Shortlist

**Files:**
- Create: `services/scanner/ranking.py`
- Create: `services/scanner/models.py`
- Modify: `services/scanner/indian_service.py`
- Modify: `shared/core/repository/beta_store.py`
- Modify: `config/nse_watchlist.json`
- Modify: `tests/beta/test_scanner_ten_stage_contract.py`
- Create: `tests/beta/test_scanner_ranking.py`
- Modify: `tests/beta/test_five_strategies.py`

**Interfaces:**
- Produces: `ScannerCandidate`, `RankingEvidence`, and `ScannerDecision` immutable values with symbol, sector, penny flag, evidence timestamp, reason codes, component scores, strategy results, and affordability preview.
- Produces: `CandidateRanker.rank(candidates: Sequence[ScannerCandidate], market_context: MarketContext) -> list[ScannerCandidate]` using liquidity, spread, momentum, NIFTY/sector relative strength, daily/weekly alignment, volatility, regime, and data quality.
- Produces: `IndianScannerService.poll(...) -> dict[str, Any]` with stages 1 through 8, at most 50 deep evaluations, and at most 10 persisted qualified signals.
- Consumes: Task 3 `EligibilityDecision` and Task 2 Scanner repository methods.

- [ ] **Step 1: Write failing deterministic ranking tests**

Build more than 50 eligible fixtures, assert exact score-component ordering, canonical-symbol tie breaking, top-50 truncation before strategy calls, sector/regime inputs, and identical output for identical evidence.

- [ ] **Step 2: Extend scanner contract tests**

Assert stages `[1,2,3,4,5,6,7,8]`, final shortlist size between zero and ten, full strategy-history requirements, score at least 85, immediate pre-publication recheck, signal cooldown/deduplication, affordability preview, and persisted accepted/rejected decisions.

- [ ] **Step 3: Add fail-closed news tests**

Assert `HIGH_RISK` rejects, provider failure records `NEWS_UNKNOWN`, and the configured hold policy prevents final qualification without discarding technical evidence.

Run: `python -m pytest tests/beta/test_scanner_ranking.py tests/beta/test_scanner_ten_stage_contract.py tests/beta/test_five_strategies.py -q`

Expected: FAIL because the scanner loops directly over a fixed watchlist, has five stages, swallows news errors, and persists every triggered strategy without broad ranking/final caps.

- [ ] **Step 4: Implement ranking and scanner orchestration**

Separate cheap eligibility and ranking from expensive strategy evaluation. Fetch daily/weekly/intraday evidence once per symbol per cycle, rank survivors, deep-evaluate only 50, and persist at most ten signals after rechecking quote freshness, spread, and all-in INR 1,000 affordability.

- [ ] **Step 5: Persist complete decision evidence**

Record each rejection and ranking with stage, code, readable message, timestamps, inputs, score components, and strategy results. Keep deterministic signal IDs and cooldown state in the shared database.

- [ ] **Step 6: Verify scanner behavior**

Run: `python -m pytest tests/beta/test_scanner_ranking.py tests/beta/test_scanner_ten_stage_contract.py tests/beta/test_five_strategies.py -q`

Expected: PASS with deterministic order and no uncontrolled network calls.

- [ ] **Step 7: Create a local checkpoint**

After preserving pre-existing Scanner/watchlist/test hunks, commit only attributable changes with `git commit -m "feat: rank and shortlist affordable NSE stocks"`; never push.

### Task 5: Paper Affordability and Persisted Daily-Loss Controls

**Files:**
- Create: `services/paper_bot/risk.py`
- Modify: `services/paper_bot/indian_service.py`
- Modify: `shared/core/repository/paper_execution_repo.py`
- Modify: `shared/core/repository/beta_store.py`
- Modify: `tests/beta/test_paper_stages_and_recovery.py`
- Create: `tests/beta/test_daily_loss_controls.py`

**Interfaces:**
- Produces: `PaperRiskPolicy.evaluate_entry(account, open_positions, signal, quote, estimated_charges, now) -> RiskDecision` enforcing INR 1,000 all-in, three open positions, one same-sector position, three consecutive losses, and daily state.
- Produces: `PaperExecutionRepository.get_daily_risk_state(trading_date: date, marks: Mapping[str, Decimal]) -> DailyRiskState` whose loss includes realized P&L, unrealized P&L, and charges.
- Produces: `PaperExecutionRepository.apply_entry_once(signal_id, position, cash_debit, risk_snapshot) -> bool` that rechecks limits and writes signal consumption, cash debit, position, and risk audit in one immediate transaction.
- Produces: `IndianPaperService.enforce_daily_limits(quotes, now) -> RiskActionReport` for warning, entry block, emergency flatten, and persisted stop.

- [ ] **Step 1: Replace the over-budget one-share test with the approved rule**

Assert a stock whose one-share price plus charges exceeds INR 1,000 is rejected as `NOT_AFFORDABLE`. Assert an affordable price selects the greatest whole quantity whose share value plus buy charges is at most INR 1,000.

- [ ] **Step 2: Write failing daily-loss threshold tests**

Cover INR 749.99 normal operation, INR 750 warning, INR 900 entry block and pending-entry cancellation, and INR 1,000 emergency stop with simulated closing fills. Include realized loss, unrealized loss, entry/exit charges, and overshoot audit.

- [ ] **Step 3: Write failing restart and session-reset tests**

Reopen the database after emergency stop and assert entries remain blocked. Advance to the next valid NSE session and assert reset occurs only after cash/position reconciliation. Test that weekends, holidays, process restarts, and Learner data cannot clear the stop.

- [ ] **Step 4: Write failing portfolio-cap tests**

Assert rejection at three open positions, a second new position from the same sector, three consecutive losing exits, negative post-charge cash, stale quote, fractional quantity, and a race where two entries compete for the last slot/cash.

Run: `python -m pytest tests/beta/test_paper_stages_and_recovery.py tests/beta/test_daily_loss_controls.py -q`

Expected: FAIL because Paper currently allows an over-budget one-share exception and has no persisted daily or portfolio risk state.

- [ ] **Step 5: Implement pure risk decisions and transactional enforcement**

Use `Decimal` and Asia/Kolkata trading dates. Evaluate once for an operator-readable decision, then recheck the same invariants inside `BEGIN IMMEDIATE` before debit. Remove the minimum-one-share exception completely.

- [ ] **Step 6: Implement warning, block, and emergency actions**

Persist the warning once, reject/cancel new entries at INR 900, and at INR 1,000 cancel entries and close open paper positions through deterministic simulated market fills. Record slippage/overshoot and keep the stop active until reconciled next-session reset.

- [ ] **Step 7: Verify Paper controls**

Run: `python -m pytest tests/beta/test_paper_stages_and_recovery.py tests/beta/test_daily_loss_controls.py -q`

Expected: PASS, including concurrent entry and restart cases.

- [ ] **Step 8: Create a local checkpoint**

After preserving pre-existing Paper hunks, commit only attributable changes with `git commit -m "feat: enforce beta paper loss limits"`; never push.

### Task 6: Learner Safety Ceiling and Calibration Validation

**Files:**
- Create: `services/learner/safety.py`
- Modify: `services/learner/indian_service.py`
- Modify: `shared/core/repository/beta_store.py`
- Modify: `tests/beta/test_learner_stages.py`
- Create: `tests/beta/test_learner_safety_ceiling.py`

**Interfaces:**
- Produces: `CalibrationSafety.validate(candidate: Mapping[str, Any], last_safe: Mapping[str, Any]) -> CalibrationDecision` allowing only bounded ranking weights, strategy weight multipliers, and confluence thresholds.
- Produces: forbidden-key rejection for database paths, price bounds, allocation, cash, daily thresholds, portfolio caps, penny-stock gates, execution mode, leverage, shorts, and live trading.
- Produces: `save_calibration_if_safe(candidate, evidence, now) -> bool`, which atomically persists validated evidence and promotion while retaining the last safe calibration on failure.

- [ ] **Step 1: Write failing allowlist and bound tests**

Assert safe finite allowlisted values pass. Assert unknown keys, non-finite values, out-of-range weights/thresholds, and attempts to alter any Scanner/Paper hard safety rule fail with explicit codes.

- [ ] **Step 2: Write failing evidence and rollback tests**

Assert insufficient trades, malformed journals, missing scanner evidence, look-ahead data, failed walk-forward results, or failed paper canaries retain the last safe calibration and create a rejection audit rather than promotion.

Run: `python -m pytest tests/beta/test_learner_stages.py tests/beta/test_learner_safety_ceiling.py -q`

Expected: FAIL because current Learner calculates and immediately promotes one calibration without a hard-key filter or last-safe promotion boundary.

- [ ] **Step 3: Implement calibration safety**

Keep the safety validator pure and independent of configuration mutation. Reject the whole candidate when any key/value is invalid; never partially apply a proposal.

- [ ] **Step 4: Wire audited promotion and rollback**

Load Scanner and Paper evidence from the shared database through read methods, validate chronologically, write proposal/evidence/result in one transaction, and expose only the last promoted safe calibration to Scanner.

- [ ] **Step 5: Verify Learner safety**

Run: `python -m pytest tests/beta/test_learner_stages.py tests/beta/test_learner_safety_ceiling.py -q`

Expected: PASS with immutable INR 250, INR 1,000 trade, and INR 1,000 daily-loss ceilings.

- [ ] **Step 6: Create a local checkpoint**

After diff attribution, commit only Task 6 changes with `git commit -m "feat: constrain beta learner calibrations"`; never push.

### Task 7: Runtime Wiring, Shared-Database End-to-End Proof, and Documentation

**Files:**
- Modify: `services/runtime.py`
- Modify: `tests/e2e/test_beta_ten_stage_paper_flow.py`
- Create: `tests/e2e/test_beta_shared_database_risk_flow.py`
- Modify: `README.md`
- Modify: `docs/BETA_V1_VERIFICATION.md`

**Interfaces:**
- Produces: `initialize_runtime_database(config: AppConfig) -> None`, run once before any service loop or `run_all()` gather.
- Produces: Scanner, Paper, and Learner each opening a separate connection through `open_beta_database(config.db_path)` while sharing the same file and repository contracts.
- Produces: deterministic offline proof from universe evidence through Scanner shortlist, Paper risk lifecycle, journal, and Learner audit.

- [ ] **Step 1: Rewrite the end-to-end fixture around one temporary file**

Use three independent connections to one file, mocked market/news/universe evidence, an INR 0.25 penny candidate that passes strict gates, an ineligible control candidate, and no external network calls.

- [ ] **Step 2: Add the complete risk-flow test**

Assert Scanner evaluates at most 50 and persists at most 10; Paper keeps each entry all-in at or below INR 1,000; accumulated realized/unrealized loss and charges trigger INR 750/900/1,000 actions; restart preserves emergency stop; Learner cannot change hard limits.

- [ ] **Step 3: Run the new end-to-end tests to verify failure**

Run: `python -m pytest tests/e2e/test_beta_ten_stage_paper_flow.py tests/e2e/test_beta_shared_database_risk_flow.py -q`

Expected: FAIL because runtime still opens three database paths and the existing test assumes three isolated in-memory databases.

- [ ] **Step 4: Rewire runtime startup and loops**

Initialize/migrate once, then open service-specific connections to `config.db_path`. Replace fixed watchlist loading with the persisted NSE universe, replace raw signal SQL with repository reads, pass exact risk policy values to Paper, and ensure all service shutdown paths close only their own connections.

- [ ] **Step 5: Update operator documentation**

Document shared-database backup/migration/recovery, Scanner rule order, penny-stock warnings, INR 1,000 trade and daily limits, emergency-stop reset, public-data limitations, paper-only boundary, credential rotation requirement, and explicit no-push/no-deployment status.

- [ ] **Step 6: Run focused and full verification**

Run: `python -m pytest tests/beta/test_shared_database.py tests/beta/test_scanner_eligibility.py tests/beta/test_scanner_ranking.py tests/beta/test_scanner_ten_stage_contract.py tests/beta/test_paper_stages_and_recovery.py tests/beta/test_daily_loss_controls.py tests/beta/test_learner_stages.py tests/beta/test_learner_safety_ceiling.py tests/e2e/test_beta_ten_stage_paper_flow.py tests/e2e/test_beta_shared_database_risk_flow.py -q`

Run: `python -m pytest tests/beta tests/e2e/test_beta_ten_stage_paper_flow.py tests/e2e/test_beta_shared_database_risk_flow.py tests/shared -q`

Run: `python -m compileall -q services shared tests`

Run: `ruff check services shared tests --select=E9,F63,F7,F82 --target-version=py312`

Run: `git diff --check`

Expected: every command exits `0`; all tests are offline; no credential, database, cache, or runtime artifact is added.

- [ ] **Step 7: Verify boundaries and exact diff**

Compare `git status --short` and exact diffs with the recorded pre-work baseline. Confirm Project Alpha remains unchanged, existing user changes are preserved, only Project Beta files are attributable, and no push, deployment, or live order occurred.

- [ ] **Step 8: Record final evidence and create a local checkpoint**

Update `docs/BETA_V1_VERIFICATION.md` with commands, counts, migration results, and known public-data limitations. If dirty-hunk attribution is safe, commit only implementation-owned changes with `git commit -m "feat: complete beta scanner and risk workflow"`; otherwise leave changes uncommitted and report the precise reason. Never push.
