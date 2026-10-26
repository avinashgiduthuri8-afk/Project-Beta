# Project Beta Priority 7A — Execution Readiness and Broker-Integration Design

**Date:** 2026-10-23  
**Status:** Complete Design Specification for User Review  
**Milestone:** V1 Paper Fleet Execution & Autonomous Optimization  
**Priority:** Priority 7A (Execution Readiness and Broker-Integration Design)  
**Target Architecture:** Indian Equities (NSE/BSE Cash), Read-Only Analytics, Broker-Neutral Execution Foundation  

---

## 1. Executive Summary & Objective

PROJECT BETA has successfully implemented Priorities 1 through 6:
1. **Priority 1:** India paper-trading integrity and 10-stage deterministic execution contract.
2. **Priority 2:** Market-neutral metadata and symbol normalization.
3. **Priority 3:** Configurable scanner core with penny-stock liquidity filtering (`last_price <= INR 250`).
4. **Priority 4:** Fundamental-data and ranking foundation.
5. **Priority 5:** Historical backtesting engine with zero look-ahead bias.
6. **Priority 6:** Read-only analytics service querying signals, backtest equity curves, drawdowns, and scanner performance.

Connecting a live broker directly to the current runtime without an explicit readiness gate would introduce severe financial and architectural risks:
- Risk of ungrounded order mutations bypassing the ₹1,000 risk boundary.
- Potential cross-talk between scanning, analytics, and execution threads.
- Lack of hardened idempotency across order submission, acknowledgement, and partial fill cycles.

**Objective of Priority 7A:** Establish a comprehensive, fail-closed architectural design and security audit framework covering:
1. **API & Dashboard Architecture:** How external consumers and future frontends securely read scanner and analytics data without acquiring database write locks.
2. **Execution Architecture:** Strict broker-neutral adapter interfaces, deterministic order lifecycle state machines, and deduplicated correlation IDs.
3. **Multi-Tier Risk Controls:** Pre-trade checks, NSE market-hours enforcement, statutory Indian fee estimation, and an atomic persistent kill switch.
4. **Security & Isolation:** Environment-driven secret hygiene, access controls, zero credential leakage, and immutable audit logs.
5. **Testing & Forward Paper Gate:** Mock broker test contracts and mathematical forward qualification requirements prior to any live broker connectivity.

---

## 2. API and Dashboard Architecture

### 2.1 Architectural Decoupling
The existing `AnalyticsService` (`services/analytics/`) provides pure read-only access to `project_beta.db`. The dashboard and API layer must strictly act as a consumer and never mutate scanner or execution tables.

```
┌────────────────────────────────────────────────────────┐
│               Frontend / Dashboard UI                   │
│   (Vite / React or Streamlit / Operator Mobile Client) │
└───────────────────────────▲────────────────────────────┘
                            │ HTTP / SSE / WebSocket
┌───────────────────────────┴────────────────────────────┐
│              Beta Gateway / API Service                │
│             (FastAPI / ASGI Read-Only)                 │
└─────────────┬───────────────────────────┬──────────────┘
              │ PRAGMA query_only=ON      │
┌─────────────▼─────────────┐ ┌───────────▼──────────────┐
│   project_beta.db (WAL)   │ │    execution.db (WAL)    │
│  (Signals, Quotes, Stats) │ │ (Intents, Fills, Audits) │
└───────────────────────────┘ └──────────────────────────┘
```

### 2.2 Endpoint Specification
All endpoints are versioned under `/api/v1/beta/`:

| Method | Endpoint | Description | Query Parameters |
|---|---|---|---|
| `GET` | `/api/v1/beta/health` | Service uptime, database sizes, data freshness | None |
| `GET` | `/api/v1/beta/scanner/signals` | Latest qualified signals & rankings | `limit`, `min_score`, `strategy`, `market` |
| `GET` | `/api/v1/beta/scanner/funnel` | Pipeline stage pass/fail drop-off counts | `start_date`, `end_date` |
| `GET` | `/api/v1/beta/analytics/backtests` | Backtest summaries and strategy metrics | `strategy`, `timeframe`, `min_win_rate` |
| `GET` | `/api/v1/beta/analytics/equity-curve` | Cumulative PnL series and drawdown | `run_id`, `strategy` |
| `GET` | `/api/v1/beta/execution/intents` | Status of recorded order intents | `status`, `symbol`, `since` |
| `GET` | `/api/v1/beta/execution/summary` | Realized daily PnL, deployed capital | None |
| `POST`| `/api/v1/beta/control/kill-switch` | Operator manual emergency halt | `{"enabled": true, "reason": "..."}` |

### 2.3 Concurrency & Lock Safety
1. **Dedicated Read-Only Connection Pool:** The API process opens SQLite connections with `mode=ro` and enforces `PRAGMA query_only = ON;`.
2. **Busy Timeout & WAL Mode:** Configured with `PRAGMA busy_timeout = 5000;` and `PRAGMA journal_mode = WAL;`, preventing reader starvation during background scanner batch inserts.
3. **Real-Time Updates via Server-Sent Events (SSE):** Rather than high-frequency polling of SQLite, the API subscribes to the internal `EventBus` (`EventType.SIGNAL_GENERATED`, `EventType.EXECUTION_UPDATE`) and streams lightweight JSON events to connected clients.

---

## 3. Broker-Neutral Execution Architecture

### 3.1 Domain Contracts & Data Models
The execution boundary is completely decoupled from any specific broker SDK (Dhan, Zerodha Kite, Upstox). All execution interactions pass through immutable dataclasses defined in `services/order_execution/contracts.py`:

- **`StockSymbol`:** Normalized `(exchange="NSE", ticker="TATASTEEL", security_id="3499")`.
- **`OrderSide`:** Enum `BUY` or `SELL`.
- **`OrderType`:** Enum `MARKET` or `LIMIT`.
- **`ProductType`:** Enum `CNC` (Cash-and-Carry delivery) or `MIS` (Intraday square-off). Default is strictly `CNC`.
- **`OrderIntent`:** Validated request to place an order:
  - `intent_id`: Unique identifier (`str`, e.g. UUIDv4).
  - `signal_id`: Foreign key link to the generated scanner signal.
  - `correlation_id`: Client order identifier (`^[a-zA-Z0-9_-]{1,30}$`).
  - `symbol`: Verified `StockSymbol`.
  - `side`: `OrderSide`.
  - `quantity`: Positive integer shares (`quantity > 0`).
  - `limit_price`: Optional `Decimal` (required for `LIMIT` orders).
  - `created_at`: Aware UTC timestamp.
- **`BrokerOrderUpdate`:** Immutable receipt emitted by broker adapters:
  - `correlation_id`, `broker_order_id`, `status`.
  - `requested_quantity`, `filled_quantity`, `average_price`.
  - `occurred_at`, `error_code`, `error_message`.

### 3.2 Order Lifecycle State Machine
Every order transition must be validated against a deterministic, non-reversible state machine:

```
               ┌──────────┐
               │ CREATED  │
               └────┬─────┘
                    │ validate_transition()
               ┌────▼─────────────┐
               │  RISK_APPROVED   │
               └────┬─────────────┘
                    │
               ┌────▼─────────────┐
               │    SUBMITTING    │
               └────┬─────────────┘
                    │
       ┌────────────┼─────────────────────────┐
       │            │                         │
┌──────▼─────┐ ┌────▼──────────┐       ┌──────▼─────┐
│  REJECTED  │ │ ACKNOWLEDGED  │       │  CANCELLED │
│ (Terminal) │ └────┬──────────┘       │ (Terminal) │
└────────────┘      │                         └────────────┘
       ┌────────────┼─────────────────────────┐
       │            │                         │
┌──────▼─────┐ ┌────▼──────────┐       ┌──────▼─────┐
│   EXPIRED  │ │ PARTIALLY_    │       │   FILLED   │
│ (Terminal) │ │ FILLED        │       │ (Terminal) │
└────────────┘ └────┬──────────┘       └────────────┘
                    │
       ┌────────────┴─────────────┐
       │                          │
┌──────▼──────────┐        ┌──────▼─────┐
│    CANCELLED    │        │   FILLED   │
│   (Terminal)    │        │ (Terminal) │
└─────────────────┘        └────────────┘
```

#### Transition Invariants:
1. **Terminal State Immutability:** Once an order reaches `FILLED`, `REJECTED`, `CANCELLED`, or `EXPIRED`, no further status transitions or quantity modifications are permitted.
2. **Quantity Monotonicity:** `filled_quantity` cannot decrease across successive updates (`current.filled_quantity <= new.filled_quantity <= requested_quantity`).
3. **Idempotent Duplicate Absorption:** Re-receiving an identical update is a non-error no-op.

### 3.3 Correlation ID & Idempotency
- `correlation_id` is computed deterministically from:
  $$\text{correlation\_id} = \text{"beta-"} + \text{SHA256}(f"{signal\_id}:{symbol}:{side}:{quantity}")[:16]$$
- If an order submission fails due to a network timeout, retrying with the same `correlation_id` allows the broker adapter to query `get_order_by_correlation()` before attempting duplicate order creation.

### 3.4 Isolated Database Architecture (`execution.db`)
Execution data is housed in `data/execution.db` rather than overloading `project_beta.db`:
- **`order_intents` Table:** Stores all generated intents, correlation IDs, requested quantities, and risk approval timestamps.
- **`order_updates` Table:** Append-only log of every broker state update.
- **`execution_fills` Table:** Normalized fill events with statutory fee breakdowns (brokerage, STT, exchange, SEBI, GST, stamp duty).
- **Foreign Key Linkage:** References `signal_id` from `project_beta.db` without cross-database write locking.

---

## 4. Multi-Tier Risk Controls

Before any `OrderIntent` transitions from `CREATED` to `RISK_APPROVED`, it must pass all four deterministic gates of the `PaperRiskPolicy`:

```
Order Intent Created
        │
        ▼
[Gate 1: Market Hours Check] ────(Fail: Closed/Holiday)───► REJECTED
        │ (Pass)
        ▼
[Gate 2: Price & Capital Ceiling] ──(Fail: > ₹1,000 all-in)──► REJECTED
        │ (Pass)
        ▼
[Gate 3: Portfolio Exposure] ────(Fail: > 15 positions)───► REJECTED
        │ (Pass)
        ▼
[Gate 4: Circuit Breaker Status] ──(Fail: Loss >= ₹900)──► REJECTED
        │ (Pass)
        ▼
RISK_APPROVED ──► Ready for Broker Adapter Dispatch
```

### 4.1 Gate 1: NSE/BSE Market Hours & Calendar Gate
- **Trading Hours:** Monday through Friday, 09:15:00 to 15:30:00 IST (03:45:00 to 10:00:00 UTC).
- **Pre-Open & Post-Close Ban:** Orders arriving before 09:15:00 IST or after 15:25:00 IST are rejected to avoid off-market queue traps.
- **Holiday Calendar:** Verified against `config/nse_holidays.json`. Weekend and declared holiday orders fail closed immediately.

### 4.2 Gate 2: Capital Allocation & Statutory Charges Gate
- **Instrument Eligibility:** Cash equities only (`series="EQ"`). Stock price must satisfy $0 < \text{last\_price} \le \text{INR } 250$.
- **All-In Order Ceiling:** Total allocation per trade cannot exceed **₹1,000**, including all statutory Indian cash-equity charges:
  - Brokerage: Zero (discount broker) or flat ₹20 cap.
  - Securities Transaction Tax (STT): 0.1% on delivery buy and sell.
  - Exchange Transaction Charges: 0.00297% (NSE).
  - SEBI Turnover Charges: ₹10 per crore (0.0001%).
  - Stamp Duty: 0.015% on buy orders.
  - Goods & Services Tax (GST): 18% on (brokerage + exchange charges + SEBI charges).
  - Modeled Slippage: 0.05% per leg.
- Formulated as:
  $$\text{Effective Quantity} = \left\lfloor \frac{\text{INR } 1,000 - \text{Fixed Charges}}{\text{Price} \times (1 + \text{Variable Charges Rate})} \right\rfloor$$
  If $\text{Effective Quantity} < 1$, the order is rejected for unaffordability.

### 4.3 Gate 3: Portfolio Capacity Gate
- **Maximum Concurrent Positions:** Capped strictly at **15 positions**.
- **Single-Symbol Concentration:** No more than 1 open position per stock symbol across any strategy.

### 4.4 Gate 4: Multi-Tier Daily Loss Circuit Breaker
Daily PnL is tracked dynamically from the midnight IST baseline and survives process restarts:
1. **Tier 1 (₹750 Daily Drawdown):** Warning alert dispatched via Telegram C2. Scanner continues operating.
2. **Tier 2 (₹900 Daily Drawdown):** Entry gating engaged. All new order entries blocked; existing positions allowed to reach profit targets or trailing stops.
3. **Tier 3 (₹1,000 Daily Drawdown):** Emergency Stop triggered. All pending orders cancelled; automated trading completely halted; alert broadcasted to operator.
4. **Persistent Kill-Switch:** State stored in `beta_risk_state` table and `circuit_breaker.json` with file-lock synchronization. A daemon restart preserves the tripped state until manual operator reset via Telegram `/reset_breaker`.

---

## 5. Security & Isolation Controls

### 5.1 Credential Management
- **Zero Secrets in Repository:** API keys, secret tokens, and passwords must never be committed. Repository hygiene tests (`test_repository_hygiene.py`) audit all tracked files against forbidden keywords (`api_key`, `secret`, `coindcx`, `zerodha`).
- **Environment Isolation:** Secrets loaded exclusively through runtime environment variables (`DHAN_CLIENT_ID`, `DHAN_ACCESS_TOKEN`, `TELEGRAM_BOT_TOKEN`).
- **No Shared Keys with Project Alpha:** Project Beta maintains completely separate credential namespaces and distinct Telegram bot tokens (`8399759111:...`).

### 5.2 Access Control & Role Boundaries
- **Service Isolation:** Scanner, Paper, and Learner processes run under unprivileged Unix user `project-beta` with mode `0600` for config and `0640` for SQLite databases.
- **Telegram C2 Operator Authentication:** The Telegram bot responds exclusively to authorized user IDs (`ALLOWED_TELEGRAM_USERS = {476738400}`). Unauthorized messages are dropped silently with a security log entry.

### 5.3 Immutable Audit Logging
- Every order intent, transition, cancellation, fill, and risk block writes a structured audit log entry to `data/execution.db`:
  - `event_id`: UUIDv4.
  - `timestamp`: Microsecond-precision ISO-8601 UTC.
  - `correlation_id`: Associated intent key.
  - `event_type`: `INTENT_CREATED`, `RISK_REJECTED`, `ORDER_SUBMITTED`, `FILL_RECORDED`, etc.
  - `payload_hash`: SHA-256 of the event payload for tamper detection.

---

## 6. Testing, Mock Verification, & Forward Paper Gate

### 6.1 Deterministic Mock Broker (`MockBroker`)
Direct broker API calls are banned in tests. All execution testing utilizes `MockBroker` (`services/order_execution/broker.py`), which:
1. Runs entirely in-memory with zero network sockets or filesystem writes.
2. Generates predictable `mock-<sha256>` broker order IDs.
3. Validates order lifecycle transitions and detects correlation ID collisions.
4. Simulates partial fills, slippage, and rejected orders deterministically.

### 6.2 Pre-Live Paper Trading Validation Gate
Passing unit tests is a necessary but **insufficient** condition for live broker integration. Live trading execution remains permanently disabled until the following quantitative forward paper gate is met:

| Metric | Minimum Required Gate | Rationale |
|---|---|---|
| **Total Closed Paper Trades** | $\ge 200$ trades | Statistical significance of realized win rate |
| **Trades per Strategy** | $\ge 30$ trades each (STE, HDA, VCP, BBS, NR7) | Strategy robustness across regimes |
| **Net Profit Factor** | $\ge 1.30$ net of Indian statutory charges | Demonstrated positive expectancy after friction |
| **Max Drawdown** | $\le \text{INR } 1,500$ (10% of ₹15k capital) | Capital preservation under adverse conditions |
| **Market Regime Coverage** | At least 2 distinct monthly market regimes | Verifying edge does not depend on a single bull rally |
| **Execution Reconciliation** | 100% agreement between orders, fills, and journals | Zero data drift or unaccounted discrepancies |

---

## 7. Next Steps & Implementation Roadmap

| Sub-Phase | Focus | Deliverables | Status |
|---|---|---|---|
| **Priority 7A** | Execution Readiness & Architecture Design | Complete Design Specification (`docs/superpowers/specs/`) | ✅ Completed |
| **Priority 7B** | Broker-Neutral Execution Foundation | Contracts, State Machine, `MockBroker`, SQLite store | ✅ Completed (168/168 tests pass) |
| **Priority 7C** | Forward Paper Fleet Telemetry | Accumulate 200 paper trades via continuous single-server runtime | 🔄 Active (Phase 5) |
| **Priority 7D** | Broker Adapter Implementation | Secure Dhan v2 live order adapter gated behind operator 2FA | ⏳ Pending Paper Gate Clearance |

