---
milestone: V1 Paper Fleet Execution & Autonomous Optimization
version: 1.3.0
updated: 2026-10-26T11:58:00+05:30
---

# Roadmap — Project Beta

> **Current Milestone:** V1 Paper Fleet Execution & Autonomous Optimization  
> **Status:** 🟢 Active (Priorities 1–6 Delivered; Priority 7A/7B Designed & Verified; Priority 8 Complete)

---

## High-Level Priority Status

| Priority | Component | Status | Deliverable |
|---|---|---|---|
| **Priority 1** | India paper-trading integrity | ✅ Complete | 10-stage execution contract, sub-₹250 filter, ₹1,000 ceiling |
| **Priority 2** | Market-neutral metadata | ✅ Complete | Unified `StockSymbol`, tick size, lot size, market metadata |
| **Priority 3** | Configurable scanner core | ✅ Complete | Dynamic scanner rules, penny-stock gates, ranking engine |
| **Priority 4** | Fundamental-data and ranking | ✅ Complete | Debt/equity filters, earnings momentum, composite scoring |
| **Priority 5** | Historical backtesting engine | ✅ Complete | Zero look-ahead replay, multi-strategy backtest runner |
| **Priority 6** | Read-only analytics service | ✅ Complete | Signals, backtest curves, drawdown, trade statistics |
| **Priority 7A**| Execution Readiness & Design | ✅ Complete | Design spec, API gateway, state machine, risk gates |
| **Priority 7B**| Broker-Neutral Execution Foundation | ✅ Complete | `OrderIntent`, `BrokerOrderUpdate`, `MockBroker`, SQLite store |
| **Priority 7C**| Forward Paper Telemetry & Gate | 🔄 In Progress | Accumulating $\ge 200$ forward paper trades via `main.py` |
| **Priority 7D**| Live Broker Integration | ⏳ Not Started | Gated until $\ge 200$ paper trades & Profit Factor $\ge 1.30$ |
| **Priority 8** | Strategy & Scanner Optimization | ✅ Complete | VCP contraction decay & volume dry-up, TradingView real-time feed |

---

## Detailed Component Breakdown

### Priority 1: India Paper-Trading Integrity
**Status:** ✅ Complete  
- [x] Deterministic 10-stage execution pipeline across Scanner (1–5), Paper (6–7), and Learner (8–10).
- [x] Sub-₹250 NSE/BSE stock filtering and ₹1,000 all-in trade affordability model with full Indian charges.
- [x] Daily loss circuit breakers (Warning at ₹750, Block at ₹900, Emergency Stop at ₹1,000) surviving restarts.

### Priority 2: Market-Neutral Metadata
**Status:** ✅ Complete  
- [x] `StockSymbol` contract supporting exchange, ticker, security ID, and lot size.
- [x] NSE/BSE trading calendar and holiday integration via `config/nse_holidays.json`.
- [x] Market instrument master resolution and normalization.

### Priority 3: Configurable Scanner Core
**Status:** ✅ Complete  
- [x] Dynamic scanner rule evaluation with pluggable liquidity and spread filters.
- [x] Multi-strategy candidate qualification (`STE`, `HDA`, `VCP`, `BBS`, `NR7`).
- [x] Audited qualification decisions and deterministic candidate ranking.

### Priority 4: Fundamental-Data and Ranking Foundation
**Status:** ✅ Complete  
- [x] Fundamental metric extractors (P/E, debt-to-equity, quarterly revenue growth).
- [x] Negative earnings and over-leveraged penny-stock exclusion filters.
- [x] Composite fundamental-technical scoring model.

### Priority 5: Historical Backtesting Engine
**Status:** ✅ Complete  
- [x] Zero look-ahead bias execution: signals generated on bar $t$, entries executed at open of bar $t+1$.
- [x] Full Indian statutory friction applied on each simulated trade leg.
- [x] Strategy performance metrics (win rate, profit factor, max drawdown, Sharpe ratio).

### Priority 6: Read-Only Analytics Service
**Status:** ✅ Complete  
- [x] Unified access to scanner signals, rankings, and persisted signal history.
- [x] Backtest performance summaries, equity curves, drawdown, and trade statistics.
- [x] Data freshness telemetry and persisted scanner failure audits.
- [x] Enforced `mode=ro` and `PRAGMA query_only = ON;`.

### Priority 7A: Execution Readiness and Broker-Integration Design
**Status:** ✅ Complete  
- [x] Complete design specification delivered in `docs/superpowers/specs/2026-10-23-priority-7a-execution-readiness-design.md`.
- [x] API & Dashboard gateway architecture specified (FastAPI / ASGI, SSE event streaming, reader pool).
- [x] State transition matrix formalized with terminal state immutability.
- [x] Multi-tier risk gates: ₹1,000 all-in order ceiling, 15-position cap, NSE trading hours (09:15–15:30 IST), and persistent kill-switch.
- [x] Forward Paper Validation Gate established ($\ge 200$ paper trades, Profit Factor $\ge 1.30$).

### Priority 7B: Broker-Neutral Execution Foundation
**Status:** ✅ Complete  
- [x] Domain contracts in `services/order_execution/contracts.py`.
- [x] Deterministic offline `MockBroker` with monotonic fill enforcement in `services/order_execution/broker.py`.
- [x] Isolated execution database (`data/execution.db`) and store in `services/order_execution/store.py`.
- [x] 14 dedicated execution foundation tests passing in `tests/beta/test_execution_foundation.py`.

### Priority 7C: Forward Paper Fleet Telemetry & Validation Gate
**Status:** 🔄 In Progress  
- [x] Run consolidated single-server runtime (`python main.py`).
- [ ] Accumulate $\ge 200$ closed paper trades across the 5 approved strategies.
- [ ] Monitor realized win rate, profit factor, and statutory fee drag.
- [ ] Verify 100% reconciliation between signals, positions, orders, and journals.

### Priority 7D: Live Broker Integration
**Status:** ⏳ Scheduled (Gated)  
- [ ] Implement broker adapter for Dhan v2 / Zerodha Kite.
- [ ] Pre-live Canary testing with single micro-order (₹200) requiring operator Telegram 2FA confirmation.
- [ ] Enable live execution mode after full compliance sign-off.

### Priority 8: Strategy Refinement and Scanner Optimization
**Status:** ✅ Complete  
- [x] External repository audit and threat quarantine (`HmERro3/indian-trading-skills` Trojan dropper blocked).
- [x] VCP contraction wave ratio decay check ($T_2 \le 0.85 \times T_1$, $T_3 \le 0.85 \times T_2$) and pre-breakout volume dry-up check in `shared/strategies/vcp.py`.
- [x] Test coverage expansion with negative tests in `tests/beta/test_five_strategies.py`.
- [x] Scanner diagnostics: Identified Yahoo stale quote rejections; switched default provider to `TRADINGVIEW`.
- [x] Expanded sub-₹250 liquid cash watchlist from 16 to 31 stocks.

---

## Test Suite Summary

- **Total Tests:** 168
- **Passing:** 168 (100%)
- **Failing / Errors:** 0
- **Duration:** 4.69s

*Roadmap maintained under GSD standards.*
