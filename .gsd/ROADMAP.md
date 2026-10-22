---
milestone: V1 Paper Fleet Execution & Autonomous Optimization
version: 1.1.0
updated: 2026-10-22T15:30:00+05:30
---

# Roadmap — Project Beta

> **Current Milestone:** V1 Paper Fleet Execution & Autonomous Optimization  
> **Status:** 🟢 Active (Milestone 1 Core Delivered; Milestone 2 Forward Optimization Active)

---

## Must-Haves for Project Beta v1 Operational Success

- [x] Deterministic 10-stage execution pipeline across Scanner (1–5), Paper (6–7), and Learner (8–10). (V1-1)
- [x] Sub-₹250 NSE/BSE stock filtering and ₹1,000 all-in trade affordability model with full Indian charges. (V1-2)
- [x] Daily loss circuit breakers (Warning at ₹750, Block at ₹900, Emergency Stop at ₹1,000) surviving restarts. (V1-3)
- [x] High-speed keyless TradingView screener, batch quote collection, and Yahoo fallback. (V1-4)
- [x] Consolidated single-server process (`python main.py`) running all engines concurrently. (V1-5)
- [x] Interactive Telegram C2 mission control with rich cards, inline buttons, and emergency pause controls. (V1-6)
- [ ] Accumulation of forward paper trades across the 5 approved strategies (STE, HDA, VCP, BBS, NR7). (V1-7)
- [ ] Promotion of autonomous learner parameter calibrations based on closed paper trades. (V1-8)
- [ ] Activation of live Dhan v2 WebSocket feed upon completion of KYC segment review. (V1-9)

---

## Completed Phases

### Phase 1: Ten-Stage Deterministic Workflow & Shared Database
**Status:** ✅ Completed  
**Objective:** Establish unified SQLite storage with WAL, migrations, and the 10-stage paper trading contract.  
- [x] Plan 1.1: Deploy `open_beta_database` and schema migrations (`beta_quotes`, `beta_signals`, `beta_positions`, `beta_learner_calibrations`).
- [x] Plan 1.2: Implement 5-strategy scanner evaluation (`STE, HDA, VCP, BBS, NR7`) with deterministic candidate ranking.
- [x] Plan 1.3: Enforce strict paper-only execution mode in canonical `AppConfig`.

### Phase 2: Indian Charges Model & Risk Policy
**Status:** ✅ Completed  
**Objective:** Implement realistic Indian cash-equity charges and multi-tier circuit breakers.  
- [x] Plan 2.1: Implement `IndianEquityChargeModel` (brokerage, STT, exchange, SEBI, GST, stamp duty, slippage).
- [x] Plan 2.2: Implement `PaperRiskPolicy` with ₹1,000 order ceiling, ₹750/₹900/₹1,000 loss gates, and max 3 positions.
- [x] Plan 2.3: Verify emergency-stop persistence across process restarts.

### Phase 3: Market Data Providers (TradingView, Yahoo, Dhan)
**Status:** ✅ Completed  
**Objective:** Provide resilient, keyless, and broker-connected market data feeds.  
- [x] Plan 3.1: Build `IndianStockPublicClient` for Yahoo delayed quotes and candles.
- [x] Plan 3.2: Implement `TradingViewPublicClient` with batch quote fetching and sub-₹250 equities screener.
- [x] Plan 3.3: Implement `DhanMarketFeed` WebSocket client with automatic public candle provider fallback.

### Phase 4: Single Server Consolidation & Telegram C2
**Status:** ✅ Completed  
**Objective:** Consolidate 3 separate services into a single server runtime with bidirectional Telegram controls.  
- [x] Plan 4.1: Create unified entrypoints (`main.py` and `services/main.py`) running all engines concurrently.
- [x] Plan 4.2: Enhance `TelegramClient` with default credentials (`8399759111`, `476738400`) and live alert dispatchers.
- [x] Plan 4.3: Implement Telegram C2 polling loop, inline navigation keyboards, and live `/tv_scan` command.

---

## Active & Upcoming Phases

### Phase 5: Forward Paper Run & Fleet Telemetry Accumulation
**Status:** 🔄 In Progress  
**Objective:** Run the consolidated server continuously to accumulate forward paper trades on liquid NSE equities.  
- [x] Plan 5.1: Initialize single server runtime with 16 liquid NSE universe stocks.
- [ ] Plan 5.2: Monitor signal generation, affordable entries, and trailing exit ratchets.
- [ ] Plan 5.3: Track daily P&L and risk limit compliance.

### Phase 6: Learner Autonomous Calibration & Promotion
**Status:** ⚪ Scheduled  
**Objective:** Accumulate minimum trades (≥ 3) per strategy to trigger autonomous learning cycles.  
- [ ] Plan 6.1: Run `IndianLearnerService` audit on closed trades.
- [ ] Plan 6.2: Validate candidate parameters within [0.80x – 1.20x] boundaries.
- [ ] Plan 6.3: Promote qualified calibrations to active scanner evaluation and alert Telegram operator.

### Phase 7: Dhan KYC Activation & Production Cutover
**Status:** ⚪ Scheduled (Waiting on Dhan Compliance)  
**Objective:** Cut over real-time market data feed to Dhan WebSocket once KYC status changes `dataPlan` to Active.  
- [ ] Plan 7.1: Verify segment activation via `https://api.dhan.co/v2/profile`.
- [ ] Plan 7.2: Switch `MARKET_DATA_PROVIDER=DHAN` in `.env`.
- [ ] Plan 7.3: Validate continuous tick streaming without connection drops.

---

## Progress Summary

| Phase | Description | Status | Plans Completed |
|---|---|---|---|
| **Phase 1** | Ten-Stage Workflow & Shared DB | ✅ Completed | 3 / 3 (100%) |
| **Phase 2** | Indian Charges & Risk Policy | ✅ Completed | 3 / 3 (100%) |
| **Phase 3** | Market Data Ingestion & TV Provider | ✅ Completed | 3 / 3 (100%) |
| **Phase 4** | Single Server & Telegram C2 | ✅ Completed | 3 / 3 (100%) |
| **Phase 5** | Forward Paper Fleet Execution | 🔄 In Progress | 1 / 3 (33%) |
| **Phase 6** | Autonomous Learner Calibration | ⚪ Scheduled | 0 / 3 (0%) |
| **Phase 7** | Dhan KYC Activation & Feed Cutover | ⚪ Scheduled | 0 / 3 (0%) |

---

*Roadmap updated and maintained under GSD standards.*
