# SPEC.md — Project Beta Specification

> **Status**: `FINALIZED`
>
> ⚠️ **Planning Lock**: Formal specification defining Project Beta v1 Indian Cash-Equities Platform.

---

## 1. Vision & Architecture Overview

**Project Beta** is an offline-safe, deterministic, paper-only algorithmic trading platform engineered for the Indian equity cash markets (NSE and BSE). Built with an uncompromising focus on capital preservation, statistical expectancy, and strict Indian statutory fee realism, the system operates across a deterministic ten-stage workflow split across three core engines:

1. **Deterministic Scanner (`beta-scanner`, Stages 1–5)**:
   - Cash session & holiday validation (NSE & BSE market hours).
   - Price bounds gating (₹0.00 < Price ≤ ₹250.00) and penny-stock liquidity filters.
   - Real-time quote collection via high-speed TradingView public screener & Yahoo feeds with optional authenticated Dhan v2 market-data feed.
   - Indicator enrichment (ATR-14, RSI-14, EMA-20, SMA-20, Volume Turnover).
   - Multi-strategy evaluation across 5 active cash strategies: **STE**, **HDA**, **VCP**, **BBS**, **NR7**.
   - Deterministic candidate ranking and 30-day news sentiment & governance safety gating.

2. **Paper Execution Engine (`beta-paper`, Stages 6–7)**:
   - Idempotent signal intake and deduplication.
   - All-in trade sizing capped strictly at ₹1,000 per order including estimated Indian statutory charges (STT, exchange turnover fees, SEBI charges, GST, stamp duty, and deterministic slippage).
   - Strict daily loss management (Warning at ₹750, Entry Block at ₹900, Emergency Stop at ₹1,000).
   - Portfolio exposure locks: maximum 3 concurrent open positions, maximum 1 position per sector.
   - Continuous open position monitoring for take-profit, stop-loss, and ratcheted trailing exit triggers.

3. **Autonomous Learner Engine (`beta-learner`, Stages 8–10)**:
   - Empirical trade autopsy evaluations of completed paper cycles.
   - Safety-bounded parameter calibration proposals constrained strictly within [0.80x – 1.20x].
   - Promoted calibration persistence in the shared Beta database and instant Telegram operator alerts.

4. **Single-Server Unified Runtime & Telegram C2**:
   - Single-command server execution (`python main.py` or `python -m services.main`) concurrently running Scanner, Paper, Learner, and Telegram C2 polling.
   - Interactive Telegram bot (`8399759111:...`, Chat ID `476738400`) offering on-demand status, positions, balance, closed trades, live TradingView scans, and emergency kill switches.

---

## 2. Core Goals

1. **Sub-₹250 Indian Cash Equity Focus**: Identify and trade liquid, dynamic Indian stocks priced under ₹250 where a ₹1,000 order size acquires whole shares with positive expectancy.
2. **Deterministic Indian Charge Realism**: Accurately model full Indian cash-market statutory costs for every simulated fill so paper results reflect true post-tax/fee profitability.
3. **Keyless Market Accessibility**: Ensure zero-dependency operation via keyless public TradingView and Yahoo market data feeds, with plug-and-play Dhan WebSocket market-data capability.
4. **Resilient Capital Protection**: Enforce multi-tier circuit breakers that persist emergency stops across process restarts and prevent overexposure.
5. **Autonomous Strategy Self-Tuning**: Enable machine learning feedback to fine-tune strategy confluence thresholds and weights without manual intervention.

---

## 3. Non-Goals (Out of Scope for v1)

- **Live Broker Order Execution**: v1 is strictly paper-only; no broker order execution client or real money risk is permitted.
- **Derivatives & F&O**: No options, futures, short selling, or margin leverage; cash delivery equities only.
- **Exposed Web Portals**: No open web ports or vulnerable web dashboards; all monitoring and command & control route via encrypted Telegram API polling.
- **Multi-Server Database Fragmentation**: No separate microservice databases; all three engines synchronize through a single SQLite database with Write-Ahead Logging (WAL).

---

## 4. System Invariants & Constraints

| Invariant | Value / Limit | Enforcement Mechanism |
|---|---|---|
| `EXECUTION_MODE` | `PAPER` | Hard validation in `AppConfig.validate_v1_safety()`; rejects any non-paper mode |
| `TRADING_ENABLED` | `False` | Reject if enabled in v1 |
| `BROKER` | `PAPER` | Rejects any live broker |
| **Capital Allocation** | ₹15,000 Initial Paper Cash | Persisted in `beta_paper_account` singleton |
| **Max Order Size** | ₹1,000 all-in per trade | Enforced in `_affordable_quantity()` and `PaperRiskPolicy` |
| **Price Bounds** | ₹0.00 < Price ≤ ₹250.00 | Scanner Stage 2 gate rejects quotes outside bounds |
| **Daily Loss Circuit Breaker** | Warning: ₹750 \| Block: ₹900 \| Stop: ₹1,000 | Persisted in `beta_paper_daily_risk`; survives server restarts |
| **Portfolio Locks** | Max 3 open positions, 1 per sector | `PaperRiskPolicy` checks before every entry |
| **Consecutive Losses** | Max 3 consecutive losing trades | Pauses entries upon reaching streak limit |
| **Active Strategies** | 5 core: `STE, HDA, VCP, BBS, NR7` | Evaluated in Scanner Stage 4; score ≥ 85 required |
| **Learner Multiplier Bound** | `[0.80x – 1.20x]` | `IndianLearnerService` clamps calibration proposals |

---

## 5. Success Criteria & Qualification Metrics

- [x] **Consolidated Single Server**: `python main.py` executes Scanner, Paper, Learner, and Telegram C2 concurrently under `asyncio.gather`.
- [x] **Unified Telegram Interface**: Alerts (startup, signals, buys, exits, calibrations) and interactive buttons dispatch via Bot `8399759111` to Chat `476738400`.
- [x] **TradingView Data Integration**: Keyless screener & quote provider operational via `TradingViewPublicClient` with batch quote prefetching.
- [x] **Dhan Market Feed Integration**: Validated WebSocket client (`wss://api-feed.dhan.co`) with fallback to candle provider when KYC is pending.
- [x] **100% Test Pass Rate**: 104 unit and integration tests passing (`python -m pytest tests -q`).
- [x] **Zero Forbidden Surface**: Clean pass of `tests/beta/test_repository_hygiene.py` with zero order credentials and clean git tree.
