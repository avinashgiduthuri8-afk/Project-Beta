---
updated: 2026-10-22T15:30:00+05:30
---

# Project State — Project Beta

## Current Position

**Milestone:** V1 Paper Fleet Execution & Autonomous Optimization  
**Phase:** Phase 5: Forward Paper Fleet Execution  
**Status:** 🔄 In Progress  
**Active Server Entrypoint:** `python main.py` or `python -m services.main`  

---

## Last Actions Completed

1. **Single-Server Architecture Consolidation (Phase 4):**
   - Consolidated 3 separate services (`Scanner`, `Paper Bot`, `AI Learner`) into a single-server runtime with `asyncio.gather`.
   - Connected unified Telegram bot (`8399759111:...`, Chat ID `476738400`) running bidirectional command & control polling loop with interactive inline keyboards.
   - Verified startup, signal, buy, exit, and learner calibration alert dispatching.

2. **TradingView Market Data & Screener Engine (Phase 3):**
   - Built `TradingViewPublicClient` connecting to TradingView's official Indian equities screener endpoint.
   - Built `TradingViewMarketDataProvider` providing high-speed batch quote prefetching and live technical indicator calculations.
   - Added `/tv_scan` Telegram command and `📡 TV Screener` inline button for real-time mobile market scanning.

3. **Dhan Authentication & Status Telemetry Validation:**
   - Authenticated Client ID `1114288518` with Dhan API (HTTP 200).
   - Confirmed `dataPlan: "Deactive"` awaiting KYC compliance completion.
   - Implemented automatic fallback in `DhanMarketDataProvider` to ensure uninterrupted runtime execution.

4. **Testing & Code Quality:**
   - 104 unit and integration tests passing (`python -m pytest tests -q`).
   - Repository hygiene verified (0 order credentials, 0 forbidden strings, no `.env` or `.db` tracked).
   - Whitespace and compilation audits clean.

---

## Active Invariants & Decisions

| Invariant / Decision | Value | Scope | Rationale |
|---|---|---|---|
| `EXECUTION_MODE` | `PAPER` | Fleet-wide | Live broker execution permanently locked in v1 |
| `MARKET_DATA_PROVIDER` | `TRADINGVIEW` / `YAHOO` | Market Access | Keyless default operational while Dhan KYC is pending |
| `ORDER_SIZE_INR` | Max ₹1,000 all-in | Execution | Strict risk boundary including Indian statutory charges |
| `INITIAL_PAPER_CASH` | ₹15,000 | Account | Single shared cash pool |
| `DAILY_LOSS_LIMIT` | ₹750 warn / ₹900 block / ₹1,000 stop | Risk Policy | Hard multi-tier circuit breaker surviving restarts |
| `PORTFOLIO_CAP` | Max 15 positions | Portfolio | Expanded multi-position swing capacity |
| `ACTIVE_STRATEGIES` | `STE, HDA, VCP, BBS, NR7` | Strategy Pool | 5 verified cash equity strategies |
| `TELEGRAM_BOT` | `8399759111:...` | Command & Control | Single bot routing all alerts and operator commands |

---

## Immediate Next Steps

1. Launch single server in background or foreground via `python main.py`.
2. Monitor forward paper entries and exit trailing stops via Telegram bot.
3. Once Dhan compliance notifies of KYC activation, cut over to `MARKET_DATA_PROVIDER=DHAN`.
