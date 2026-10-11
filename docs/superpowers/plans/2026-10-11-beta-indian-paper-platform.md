# Project Beta Indian Paper Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver Project Beta v1 as a local, Indian-stock-only three-service fleet—Scanner, Paper Trading, and AI Learner—with a ten-stage paper workflow and no reachable live-order path.

**Architecture:** Keep Alpha's process and SQLite isolation pattern, but centralize NSE/BSE market data in Scanner, hand qualified signals to Paper through a read-only scanner database, and hand completed journals to Learner through read-only scanner/paper databases. Yahoo is the keyless default; an optional Dhan WebSocket is market-data-only, and the active runtime contains no broker order client.

**Tech Stack:** Python 3.12, asyncio, Pydantic Settings, httpx, websockets, aiosqlite/SQLite WAL, pytest, pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-10-11-beta-indian-paper-platform-design.md`

## Global Constraints

- Active services are ordered Scanner → Paper Trading → AI Intelligence/Learner.
- Stages 1–5 belong to Scanner, 6–7 to Paper, and 8–10 to Learner.
- NSE/BSE cash equities only; quantities are positive whole shares and currency is INR.
- Paper mode is the only v1 execution mode; Dhan order placement is deferred to Beta v2.
- Default market data is keyless Yahoo; optional Dhan credentials enable market-data WebSocket only.
- No Alpha credentials, databases, tokens, logs, or runtime state may be copied or referenced.
- No VPS connection, upload, provisioning, or deployment is authorized.
- The local artifacts must be suitable for a future 1-vCPU, 2-GB-RAM, 20-GB-SSD server.
- Preserve current uncommitted work until its intent is incorporated or replaced by a tested task.

## Review Focus

- A `NSE:`/`BSE:` symbol with whitespace, suffixes, or an unsupported exchange must normalize deterministically or fail with `INVALID_STOCK_SYMBOL`; Task 2 pins this.
- A Dhan WebSocket reconnect must not mix stale pre-disconnect ticks with the new session; Task 3 pins generation IDs and freshness.
- Restarting Paper after a signal was already consumed must not create a second position or debit cash twice; Task 5 pins persistent idempotency.
- A journal row with NaN, missing timestamps, or a non-equity symbol must never become a promoted calibration; Task 6 pins fail-closed learning.
- No imported module, configuration override, or stale deployment file may expose live order placement; Tasks 1 and 7 pin import and repository scans.

---

### Task 1: Paper-Only Service Topology and Configuration

**Files:**
- Modify: `shared/core/config.py`
- Modify: `services/scanner/config/settings.py`
- Modify: `services/paper_bot/config/settings.py`
- Modify: `services/learner/config.py`
- Modify: `services/scanner/signals/dispatcher.py`
- Modify: `tests/shared/test_canonical_config.py`
- Modify: `tests/shared/test_three_service_architecture.py`
- Create: `tests/beta/test_v1_live_surface_absent.py`
- Delete: `services/live_bot/`
- Delete: `services/live/`
- Delete: `shared/execution/brokers/`
- Delete: `shared/execution/trading/subaccount_manager.py`
- Delete: `shared/execution/trading/broker_manager.py`

**Interfaces:**
- Produces: `AppConfig` fields `execution_mode: Literal["PAPER"]`, `market_data_provider: Literal["YAHOO", "DHAN"]`, `dhan_client_id: str | None`, `dhan_access_token: str | None`, and `validate_v1_safety() -> None`.
- Produces: Scanner/Paper/Learner config defaults `scanner/data/beta_scanner.db`, `paper/data/beta_paper.db`, and `learner/data/beta_learner.db`.
- Produces: `SignalDispatcher.dispatch(signal: Signal) -> dict[str, Any]` with only a `paper` result; no `live` clone.

- [ ] **Step 1: Rewrite the architecture tests to describe the approved fleet**

Assert service order `("SCANNER", "PAPER", "LEARNER")`, isolated Beta database paths, Paper-only signal dispatch, and rejection of `LIVE` or unknown execution modes. Replace the current uncommitted Scanner/Paper/Live expectations rather than discarding the file.

- [ ] **Step 2: Add a failing live-surface test**

```python
def test_v1_has_no_live_service_or_broker_order_modules():
    assert importlib.util.find_spec("services.live_bot") is None
    assert importlib.util.find_spec("execution.brokers") is None

def test_config_rejects_live_mode():
    with pytest.raises(ValueError, match="Beta v1 is paper-only"):
        AppConfig(_env_file=None, execution_mode="LIVE")
```

- [ ] **Step 3: Run the tests to verify the old topology fails**

Run: `python -m pytest tests/shared/test_canonical_config.py tests/shared/test_three_service_architecture.py tests/beta/test_v1_live_surface_absent.py -q`

Expected: FAIL because Live is still configured/dispatched and v1 safety fields are absent.

- [ ] **Step 4: Implement the paper-only configuration and dispatch boundary**

Remove CoinDCX, Zerodha, broker-order, `trading_enabled`, live credential, live database, and live-mode configuration from active v1 settings. Make `validate_v1_safety()` reject any non-Paper execution value or order-capable provider. Remove the Live signal clone and delete the inactive Live service and broker-order packages without modifying Project Alpha.

- [ ] **Step 5: Run the topology tests**

Run: `python -m pytest tests/shared/test_canonical_config.py tests/shared/test_three_service_architecture.py tests/beta/test_v1_live_surface_absent.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the topology boundary**

```bash
git add shared/core/config.py shared/execution/brokers shared/execution/trading/subaccount_manager.py shared/execution/trading/broker_manager.py services/scanner/config/settings.py services/paper_bot/config/settings.py services/learner/config.py services/scanner/signals/dispatcher.py services/live_bot services/live tests/shared/test_canonical_config.py tests/shared/test_three_service_architecture.py tests/beta/test_v1_live_surface_absent.py
git commit -m "refactor: make Beta v1 paper-only"
```

### Task 2: Indian Stock Domain and Persistence

**Files:**
- Create: `shared/market/symbols.py`
- Modify: `shared/market/__init__.py`
- Modify: `shared/core/types.py`
- Modify: `shared/core/repository/migrations/001_core_tables.sql`
- Modify: `shared/core/repository/migrations/005_candle_history.sql`
- Modify: `shared/core/repository/migrations/008_post_trade_journal.sql`
- Modify: `shared/core/repository/signal_repo.py`
- Modify: `shared/core/repository/position_repo.py`
- Modify: `shared/core/repository/trade_repo.py`
- Modify: `shared/core/repository/candle_repo.py`
- Modify: `shared/core/repository/journal_repo.py`
- Create: `tests/beta/test_stock_domain_and_repositories.py`

**Interfaces:**
- Produces: immutable `StockSymbol(exchange: Literal["NSE", "BSE"], trading_symbol: str, security_id: str | None = None)`.
- Produces: `StockSymbol.parse(raw: str, default_exchange: str = "NSE") -> StockSymbol` and `.canonical -> str`.
- Produces: canonical `Signal`, `Position`, and `Trade` stock fields `symbol`, `exchange`, and optional `security_id`; persistence round-trips these fields.
- Consumes: Paper-only modes from Task 1.

- [ ] **Step 1: Write failing symbol and repository tests**

Test `NSE:RELIANCE`, `RELIANCE.NS`, `BSE:500325`, whitespace/case normalization, invalid `NASDAQ:AAPL`, empty symbols, and persistence round-trips. Assert quantities reject zero, negative, fractional, NaN, and infinity.

- [ ] **Step 2: Run the domain tests and confirm failure**

Run: `python -m pytest tests/beta/test_stock_domain_and_repositories.py -q`

Expected: FAIL because `StockSymbol` and stock repository fields do not exist.

- [ ] **Step 3: Implement `StockSymbol` and stock-first domain types**

Use `Decimal` for tick/quantity boundary validation. Keep read-only `coin`/`pair` compatibility properties only where required to migrate retained strategy code; new provider payloads and persistence must use `symbol`, `exchange`, and `security_id`.

- [ ] **Step 4: Convert fresh-database migrations and repositories**

Replace crypto-named columns in signals, positions, trades, candles, and journal schemas. Because Beta old runtime data was explicitly discarded, do not add a legacy data-copy migration. Preserve schema version ordering and foreign-key/index behavior.

- [ ] **Step 5: Run repository and core regression tests**

Run: `python -m pytest tests/beta/test_stock_domain_and_repositories.py tests/shared/test_candle_persistence.py tests/shared/test_order_idempotency.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the stock domain**

```bash
git add shared/market/symbols.py shared/market/__init__.py shared/core/types.py shared/core/repository tests/beta/test_stock_domain_and_repositories.py
git commit -m "feat: add canonical Indian stock domain"
```

### Task 3: Centralized Yahoo and Dhan Market Data

**Files:**
- Modify: `requirements.txt`
- Create: `shared/market/models.py`
- Create: `shared/market/provider.py`
- Create: `shared/market/instruments.py`
- Create: `shared/market/dhan_feed.py`
- Create: `shared/core/repository/quote_repo.py`
- Create: `shared/core/repository/migrations/017_market_quotes.sql`
- Modify: `shared/market/public_client.py`
- Modify: `shared/market/feeder.py`
- Modify: `shared/market/__init__.py`
- Replace: `tests/beta/test_indian_market_and_brokers.py`
- Create: `tests/beta/test_market_data_providers.py`

**Interfaces:**
- Produces: `MarketQuote(symbol: StockSymbol, price: Decimal, timestamp: datetime, source: str, generation: int)` with `is_fresh(now: datetime, max_age: timedelta) -> bool`.
- Produces: `MarketDataProvider` protocol methods `get_quote(symbol)`, `get_candles(symbol, timeframe, limit)`, and `close()`.
- Produces: `DhanInstrumentMaster.refresh() -> None` and `resolve(symbol: StockSymbol) -> StockSymbol` using the public compact instrument CSV.
- Produces: `DhanMarketFeed.start(symbols, on_quote)`, `stop()`, and `health() -> dict[str, Any]`; it has no order methods.
- Produces: `MarketFeeder.get_latest_quote(symbol) -> MarketQuote | None` as Scanner's single data entry point.
- Produces: `QuoteRepository.upsert(quote: MarketQuote) -> None` and `get_latest(canonical_symbol: str) -> MarketQuote | None` for read-only Paper consumption.
- Consumes: `StockSymbol` from Task 2 and provider settings from Task 1.

- [ ] **Step 1: Replace broker-order tests with provider tests**

Preserve the existing Yahoo parsing/session/precision coverage, delete Zerodha and Dhan order assertions, and add Dhan instrument parsing, authenticated WebSocket subscription, heartbeat/reconnect, stale-tick, and generation-reset tests using fakes.

- [ ] **Step 2: Run the provider tests to verify failure**

Run: `python -m pytest tests/beta/test_indian_market_and_brokers.py tests/beta/test_market_data_providers.py -q`

Expected: FAIL because the provider protocol, instrument master, and Dhan feed do not exist and broker order tests still describe v2 behavior.

- [ ] **Step 3: Implement provider models and Yahoo caching**

Add `websockets>=15,<16`. Give Yahoo a bounded TTL cache keyed by canonical symbol/timeframe/limit, coalesce concurrent identical requests, preserve rate limiting, and never return a quote older than the configured freshness threshold as current.

- [ ] **Step 4: Implement market-data-only Dhan integration**

Parse only NSE/BSE `EQUITY`/cash rows from the public master. Use one WebSocket, bounded exponential reconnect, a monotonically increasing generation, and discard cached quotes from earlier generations after disconnect. Do not import or implement Dhan order endpoints.

- [ ] **Step 5: Make `MarketFeeder` the single provider owner**

Select Yahoo or Dhan from `AppConfig`, store bounded quote/candle caches, persist the latest quote through `QuoteRepository`, expose health/freshness, and ensure Paper/Learner have no provider imports.

- [ ] **Step 6: Run the provider suite**

Run: `python -m pytest tests/beta/test_indian_market_and_brokers.py tests/beta/test_market_data_providers.py -q`

Expected: PASS without network access.

- [ ] **Step 7: Commit market data**

```bash
git add requirements.txt shared/market tests/beta/test_indian_market_and_brokers.py tests/beta/test_market_data_providers.py
git commit -m "feat: add centralized Indian stock market data"
```

### Task 4: Scanner Stages 1–5

**Files:**
- Modify: `services/scanner/main.py`
- Modify: `services/scanner/service.py`
- Modify: `services/scanner/market_context.py`
- Modify: `services/scanner/validation.py`
- Modify: `services/scanner/adapter.py`
- Modify: `services/scanner/signals/dispatcher.py`
- Modify: `services/scanner/confluence_engine.py`
- Modify: `services/scanner/news_fetcher.py`
- Modify: `services/scanner/sentiment_analyzer.py`
- Modify: `config/nse_watchlist.json`
- Create: `tests/beta/test_scanner_ten_stage_contract.py`
- Modify: relevant tests under `tests/scanner/`

**Interfaces:**
- Produces: `ScannerService.poll(max_timestamp_ms: int | None = None) -> dict[str, Any]` whose result records stage outcomes `1` through `5`.
- Produces: persisted qualified `Signal` rows with canonical symbol, market-data timestamp/source, strategy, score, expiry, and rejection audit.
- Consumes: `MarketFeeder` from Task 3, stock types/repositories from Task 2, and promoted calibration cache read-only.

- [ ] **Step 1: Add a failing stage-contract test**

Feed deterministic NSE candles and assert stage order `[1, 2, 3, 4, 5]`, closed-market/stale-data rejection codes, NIFTY/BANKNIFTY context, duplicate suppression, and a qualified `NSE:RELIANCE` signal persisted exactly once.

- [ ] **Step 2: Run focused Scanner tests to capture failures**

Run: `python -m pytest tests/beta/test_scanner_ten_stage_contract.py tests/scanner -q`

Expected: FAIL on CoinDCX pair conversion, BTC/ETH context, crypto news, and coin-shaped payloads.

- [ ] **Step 3: Convert Scanner inputs and stages 1–3**

Delete `canonical_to_coindcx_pair()` and `_fetch_coindcx_candles()`. Read only from `MarketFeeder`; apply NSE/BSE session, freshness, liquidity/turnover, candle integrity, trend, volatility, and confluence gates with explicit stage/rejection records.

- [ ] **Step 4: Convert stages 4–5 and market context**

Run the retained eight strategies on canonical stock symbols, rank candidates, apply expiry/cooldown/idempotency, and persist only Paper signals. Replace crypto sentiment/news terminology and BTC/ETH benchmarks with NIFTY 50/BANK NIFTY; disable optional news cleanly when no source is configured.

- [ ] **Step 5: Run Scanner tests**

Run: `python -m pytest tests/beta/test_scanner_ten_stage_contract.py tests/scanner -q`

Expected: PASS with no external network access.

- [ ] **Step 6: Commit Scanner stages**

```bash
git add services/scanner config/nse_watchlist.json tests/scanner tests/beta/test_scanner_ten_stage_contract.py
git commit -m "feat: convert scanner stages to Indian equities"
```

### Task 5: Paper Stages 6–7 and Restart Safety

**Files:**
- Create: `shared/execution/indian_charges.py`
- Create: `shared/core/repository/paper_execution_repo.py`
- Create: `services/paper_bot/execution/fill_simulator.py`
- Modify: `services/paper_bot/main.py`
- Modify: `services/paper_bot/execution/router.py`
- Modify: `services/paper_bot/portfolio/qpb.py`
- Modify: `shared/execution/auto_trader.py`
- Modify: `shared/execution/position_manager.py`
- Modify: `shared/execution/risk/capital_guard.py`
- Modify: `shared/background/journal/service.py`
- Modify: `shared/background/portfolio/service.py`
- Create: `tests/beta/test_paper_stages_and_recovery.py`
- Modify: relevant tests under `tests/paper_bot/` and `tests/shared/`

**Interfaces:**
- Produces: `IndianEquityChargeModel.estimate(side, price, quantity, exchange, intraday=False) -> ChargeBreakdown` using named STT, stamp, exchange, SEBI, GST, brokerage, and slippage fields.
- Produces: `PaperFillSimulator.fill(order, quote, now) -> PaperFill | None` with explicit Market/Limit/Stop rules and quote timestamps.
- Produces: `PaperExecutionRepository.open_position_once(signal_id, position, cash_debit) -> bool`, which atomically records signal consumption, cash debit, and position creation.
- Produces: `PaperExecutionRouter.handle_signal_event(...)` stage results `6` and `7`, persistent idempotency, whole-share allocation, and INR cash accounting.
- Consumes: qualified Scanner signals and persisted quotes; it must not import a market provider.

- [ ] **Step 1: Write failing charge/fill/risk/restart tests**

Cover BUY/SELL charge components, tick rounding, insufficient cash, excessive exposure, fractional quantity rejection, closed/stale quote rejection, limit and stop fills, exit P&L, two restarts consuming one signal, and recovery of existing positions before polling.

- [ ] **Step 2: Run Paper tests and confirm failure**

Run: `python -m pytest tests/beta/test_paper_stages_and_recovery.py tests/paper_bot -q`

Expected: FAIL because Paper still fetches CoinDCX tickers, uses crypto quantities/friction, and lacks durable consumption semantics.

- [ ] **Step 3: Implement Indian charge and fill models**

Use `Decimal` and explicit rounding. Put rates in immutable configuration with test-pinned defaults; label all results estimates and exclude income tax. Market fills use the first fresh eligible recorded quote, while Limit/Stop decisions use candle high/low plus deterministic slippage rules.

- [ ] **Step 4: Convert risk, allocation, lifecycle, and journal**

Apply Stage 6 before debiting paper cash. Stage 7 creates simulated orders/positions, monitors stored Scanner prices, closes positions, records every charge component, and persists a consumed-signal/idempotency record in the same transaction as the position creation.

- [ ] **Step 5: Remove all Paper provider and broker calls**

Delete the CoinDCX HTTP mark-to-market request from `services/paper_bot/main.py`. Paper reads Scanner's persisted quote/candle evidence through its read-only database connection.

- [ ] **Step 6: Run Paper and shared safety tests**

Run: `python -m pytest tests/beta/test_paper_stages_and_recovery.py tests/paper_bot tests/shared/test_safety_invariants.py tests/shared/test_order_idempotency.py tests/shared/test_portfolio_service.py -q`

Expected: PASS.

- [ ] **Step 7: Commit Paper stages**

```bash
git add shared/execution shared/background/journal shared/background/portfolio services/paper_bot tests/paper_bot tests/shared tests/beta/test_paper_stages_and_recovery.py
git commit -m "feat: implement Indian equity paper lifecycle"
```

### Task 6: AI Learner Stages 8–10

**Files:**
- Modify: `services/learner/main.py`
- Modify: `services/learner/service.py`
- Modify: `shared/background/learning/engine.py`
- Modify: `shared/background/learning/calibrator.py`
- Modify: `shared/background/backtest/service/service.py`
- Modify: `shared/background/feedback/orchestrator.py`
- Modify: `shared/core/repository/learning_repo.py`
- Modify: `shared/core/repository/feedback_repo.py`
- Modify: `shared/core/repository/backtest_repo.py`
- Create: `tests/beta/test_learner_stages.py`
- Modify: relevant tests under `tests/learner/` and `tests/shared/`

**Interfaces:**
- Produces: `LearnerService.run_cycle(limit: int = 100) -> dict[str, Any]` with stage results `8`, `9`, and `10` and statuses `COMPLETED`, `INSUFFICIENT_DATA`, or `FAILED`.
- Produces: promoted calibrations restricted to allowlisted strategy weight multipliers and confluence thresholds.
- Consumes: completed Paper journal rows and Scanner candles read-only.

- [ ] **Step 1: Write failing Learner safety tests**

Assert rejection of duplicate/incomplete/non-finite/non-equity journals, minimum 60 candles, 70/30 chronological split, minimum two trades and profit factor `0.8` in each segment, five canary outcomes and profit factor `1.0` for promotion, and rollback on two consecutive losses or rolling profit factor below `0.8`.

- [ ] **Step 2: Run Learner tests and verify failures**

Run: `python -m pytest tests/beta/test_learner_stages.py tests/learner -q`

Expected: FAIL on crypto defaults such as `BTC/INR`, incomplete equity validation, or missing explicit stage results.

- [ ] **Step 3: Convert Stage 8 post-trade diagnosis**

Accept only completed NSE/BSE stock journals, derive stable incident IDs from strategy/canonical symbol/pattern/source trades, and keep immutable safety fields outside the proposal schema.

- [ ] **Step 4: Convert Stage 9 and Stage 10 validation**

Load canonical stock candles, enforce chronological no-look-ahead validation, persist every accept/reject reason, promote only through paper canaries, and retain last-known-safe calibrations after corrupt or interrupted cycles.

- [ ] **Step 5: Run Learner tests**

Run: `python -m pytest tests/beta/test_learner_stages.py tests/learner tests/shared/test_learning_safety_boundary.py tests/shared/test_backtest_historical.py -q`

Expected: PASS.

- [ ] **Step 6: Commit Learner stages**

```bash
git add services/learner shared/background/learning shared/background/backtest shared/background/feedback shared/core/repository tests/learner tests/shared tests/beta/test_learner_stages.py
git commit -m "feat: adapt learner stages to stock paper trades"
```

### Task 7: Remove Legacy Runtime Surfaces and Prepare Local Deployment Assets

**Files:**
- Delete or reduce to Paper-only code: `shared/execution/service.py`
- Delete: `shared/execution/reconciliation.py`
- Delete: `shared/execution/recovery.py`
- Delete: `shared/background/production/`
- Delete: crypto/live-only tests under `tests/live_bot/` and obsolete CoinDCX tests under `tests/shared/`
- Delete: `deploy/systemd/alpha-live.service`
- Delete: `deploy/scripts/scripts/deploy_and_wipe_vps.py`
- Delete: `deploy/scripts/scripts/deploy_vps.sh`
- Modify: `deploy/docker/docker-compose.yml`
- Modify: `deploy/docker/Dockerfile`
- Rename/modify: `deploy/systemd/alpha-scanner.service` → `deploy/systemd/beta-scanner.service`
- Rename/modify: `deploy/systemd/alpha-paper.service` → `deploy/systemd/beta-paper.service`
- Rename/modify: `deploy/systemd/alpha-learner.service` → `deploy/systemd/beta-learner.service`
- Modify: `deploy/env/scanner.env.example`
- Modify: `deploy/env/paper.env.example`
- Modify: `deploy/env/learner.env.example`
- Modify: `shared/core/logging.py`
- Modify: `README.md`
- Modify: `docs/INDIAN_MARKET_ADAPTATION.md`
- Create: `tests/beta/test_repository_hygiene.py`

**Interfaces:**
- Produces: three local deployment definitions named `beta-scanner`, `beta-paper`, and `beta-learner`, with read-only cross-service mounts and bounded CPU/memory/log settings.
- Produces: rotating logs and documented retention compatible with 20 GB storage.
- Consumes: all active runtime surfaces from Tasks 1–6.

- [ ] **Step 1: Add a failing repository-hygiene test**

Scan active Python/config/deployment paths and assert there are no CoinDCX endpoints, crypto pairs, Zerodha/INDstocks adapters, live service units, order HTTP methods, Alpha database paths, embedded secrets, or VPS deployment scripts. Explicitly allow historical design/spec text when it explains removals.

- [ ] **Step 2: Run the hygiene test and capture the legacy inventory**

Run: `python -m pytest tests/beta/test_repository_hygiene.py -q`

Expected: FAIL and list remaining active legacy paths.

- [ ] **Step 3: Remove unreachable legacy modules and tests**

Delete live/broker/CoinDCX runtime code after confirming no active import remains. Convert retained generic modules and tests to stock terminology; do not mechanically rewrite historical Git data or Project Alpha.

- [ ] **Step 4: Update local deployment artifacts for the future resource envelope**

Define only the three approved services. Cap combined memory below 2 GB, use bounded log rotation, mount Scanner and Paper databases read-only for consumers, and remove commands that contact or mutate a VPS. These are local artifacts only; do not deploy them.

- [ ] **Step 5: Update docs and examples**

Document local startup order, keyless Yahoo mode, optional Dhan market-data credentials, Paper-only safety, ten stages, database ownership, resource expectations, and the explicit Beta v2 live-order deferral.

- [ ] **Step 6: Run hygiene and service-boundary tests**

Run: `python -m pytest tests/beta/test_repository_hygiene.py tests/shared/test_three_service_architecture.py -q`

Expected: PASS.

- [ ] **Step 7: Commit cleanup and local assets**

```bash
git add -A
git commit -m "chore: remove legacy crypto and live surfaces"
```

### Task 8: End-to-End Verification and Release Documentation

**Files:**
- Create: `tests/e2e/test_beta_ten_stage_paper_flow.py`
- Create: `docs/BETA_V1_VERIFICATION.md`
- Modify: `README.md`

**Interfaces:**
- Produces: deterministic offline proof that one NSE signal passes stages 1–7, one completed paper trade passes stages 8–10, and each database contains only its owned writes.
- Consumes: all interfaces from Tasks 1–7.

- [ ] **Step 1: Write the failing offline end-to-end test**

Use fake Yahoo/Dhan events and temporary SQLite databases. Assert exact stage sequence `[1,2,3,4,5,6,7,8,9,10]`, a whole-share paper position, charge-adjusted INR P&L, a persisted learning audit, no external network call, and no duplicate work after restarting all three services.

- [ ] **Step 2: Run the end-to-end test and fix only integration defects**

Run: `python -m pytest tests/e2e/test_beta_ten_stage_paper_flow.py -q`

Expected: PASS after resolving wiring defects without expanding scope.

- [ ] **Step 3: Run the complete verification suite**

Run: `python -m compileall -q services shared tests deploy`

Run: `python -m pytest tests/beta tests/scanner tests/paper_bot tests/learner tests/e2e/test_beta_ten_stage_paper_flow.py -q`

Run: `python -m pytest tests/shared -q`

Run: `ruff check services shared tests deploy --select=E9,F63,F7,F82 --target-version=py312`

Expected: all commands exit `0` with no failed tests or critical static errors.

- [ ] **Step 4: Verify repository and source-project safety**

Run repository hygiene/secret scans, `git status --short`, and `git -C ../PROJECT-ALPHA status --short --branch`. Confirm Beta contains no runtime databases, environment files, tokens, or order endpoint, and Alpha remains unchanged.

- [ ] **Step 5: Record reproducible evidence**

Write `docs/BETA_V1_VERIFICATION.md` with command outputs, test counts, known external prerequisites for optional Dhan data, and an explicit statement that no VPS upload or live order occurred.

- [ ] **Step 6: Commit verified v1**

```bash
git add tests/e2e/test_beta_ten_stage_paper_flow.py docs/BETA_V1_VERIFICATION.md README.md
git commit -m "test: verify Beta v1 paper workflow"
```

- [ ] **Step 7: Stop before external actions**

Report commits and verification evidence. Request explicit user approval before any push. Do not connect to a server; cloud work begins only after the user supplies the new server following code completion.
