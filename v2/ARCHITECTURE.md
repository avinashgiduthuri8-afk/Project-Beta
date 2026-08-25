# PROJECT-ALPHA V2 — Complete Architecture

> **Phase 1: Architecture Definition**  
> **Status**: Awaiting approval before any implementation code is written.  
> **Constraint**: V1 is frozen. Zero V1 files modified. V2 is purely additive.

---

## Table of Contents

1. [Design Principles](#1-design-principles)
2. [Folder Structure](#2-folder-structure)
3. [Shared Core](#3-shared-core)
4. [Event Bus — Full Definition](#4-event-bus--full-definition)
5. [Service Layer](#5-service-layer)
6. [Repository Layer](#6-repository-layer)
7. [SQLite Schema](#7-sqlite-schema)
8. [Configuration System](#8-configuration-system)
9. [Background Scheduler](#9-background-scheduler)
10. [WebSocket Push Feed](#10-websocket-push-feed)
11. [Monitoring / Metrics](#11-monitoring--metrics)
12. [Data Flow](#12-data-flow)
13. [Migration Roadmap V1 → V2](#13-migration-roadmap-v1--v2)

---

## 1. Design Principles

### What V1 Got Right
- Bots are correctly separated — MTB, PMB, VGX each own their trading logic.
- The risk engine is a shared gatekeeper — this pattern is kept and promoted.
- The scanner is already service-shaped (FastAPI over HTTP).
- Paper/Live mode per bot is the right mental model.

### What V1 Accumulated as Debt
| Problem | Root Cause | V2 Fix |
|---|---|---|
| Each bot has its own JSON files + locking code | No shared storage layer | SQLite via Repository Layer |
| Risk engine imports bot storage modules directly | Tight coupling between services | Risk Service reads from Repository |
| Dashboard polls bots via `asyncio.to_thread` every 3s | No event system | WebSocket push via Event Bus |
| 4× Telegram bots with duplicated dispatch code | No shared notification layer | Single Notification Service |
| Per-bot `config.py` with overlapping env vars | No unified config | `V2Config` Pydantic model |
| Background loops are raw `asyncio.sleep` in bot code | No scheduler | Background Scheduler with job registry |
| No audit trail — no record of why a decision was made | No event log | `event_log` table captures all events |
| VGX scanner bridge does a self-referential HTTP call | No internal messaging | Event Bus replaces HTTP polling |

### V2 Architecture Pillars
1. **Event-Driven**: Every state change is a typed `EventType` on the shared bus. No direct imports between services.
2. **Repository over File I/O**: All persistence goes through a Repository. Services never touch files directly.
3. **Single Config Source of Truth**: One `V2Config` object, validated at startup, passed by injection — no `os.getenv` scattered across 20 files.
4. **Push over Poll**: Dashboard clients receive events over WebSocket. The server never has to aggregate on a timer.
5. **Additive Migration**: V1 runs identically. V2 wraps V1 APIs (HTTP) and progressively takes over ownership service by service.

---

## 2. Folder Structure

```
v2/                                   ← Root of all V2 code (no V1 imports)
│
├── __init__.py                       ← version = "2.0.0-alpha"
│
├── core/                             ← Shared primitives — no business logic
│   ├── __init__.py
│   ├── config.py                     ← V2Config (Pydantic settings model)
│   ├── exceptions.py                 ← V2 exception hierarchy
│   ├── logging.py                    ← Structured JSON logger factory
│   └── types.py                      ← Domain types: Signal, Position, Trade, BotMode…
│
├── bus/                              ← Event Bus (V2.0 skeleton already exists)
│   ├── __init__.py                   ← exports: bus, EventType
│   ├── event_bus.py                  ← EventBus class (existing — functional)
│   ├── event_types.py                ← EventType enum (expand from 16 → 28 events)
│   └── subscribers.py                ← register_all() wires services to events
│
├── services/                         ← Business logic, one service per domain
│   ├── __init__.py
│   │
│   ├── scanner_service/              ← Signal sourcing
│   │   ├── __init__.py
│   │   ├── service.py                ← ScannerService: polls V1 API, publishes to bus
│   │   ├── adapter.py                ← V1 HTTP response → V2 Signal type
│   │   └── signal_filter.py          ← Priority/staleness filtering
│   │
│   ├── risk_service/                 ← Capital enforcement, circuit breaker
│   │   ├── __init__.py
│   │   ├── service.py                ← RiskService: check_trade_allowed()
│   │   ├── capital_guard.py          ← CapitalGuard: per-bot + total limit logic
│   │   └── circuit_breaker.py        ← CircuitBreaker: halt all bots on trigger
│   │
│   ├── portfolio_service/            ← Position tracking, PnL, AUM
│   │   ├── __init__.py
│   │   ├── service.py                ← PortfolioService: subscribes POSITION_*
│   │   └── aggregator.py             ← Cross-bot capital deployed, cash, PnL
│   │
│   ├── trading_service/              ← Execution adapters for each bot
│   │   ├── __init__.py
│   │   ├── service.py                ← TradingService: signal → decision → execute
│   │   ├── mtb_adapter.py            ← Wraps V1 MTB trading engine over HTTP
│   │   ├── pmb_adapter.py            ← Wraps V1 PMB trading engine over HTTP
│   │   └── vgx_adapter.py            ← Wraps V1 VGX trading engine over HTTP
│   │
│   ├── dashboard_service/            ← API aggregation and WebSocket feed
│   │   ├── __init__.py
│   │   ├── service.py                ← DashboardService: state snapshot builder
│   │   ├── websocket.py              ← WebSocket connection manager
│   │   └── schemas.py                ← Pydantic response models for V2 API
│   │
│   └── notification_service/         ← Unified alert dispatch
│       ├── __init__.py
│       ├── service.py                ← NotificationService: subscribes ALERT_GENERATED
│       ├── telegram.py               ← Single Telegram dispatcher (replaces 4× bots)
│       └── formatters.py             ← Per-event message templates
│
├── repository/                       ← Persistence — all SQL, no business logic
│   ├── __init__.py
│   ├── base.py                       ← BaseRepository ABC
│   ├── db.py                         ← SQLite connection pool, schema migrations
│   ├── position_repo.py              ← PositionRepository: CRUD + queries
│   ├── trade_repo.py                 ← TradeRepository: closed trade history
│   ├── signal_repo.py                ← SignalRepository: signal lifecycle
│   ├── metrics_repo.py               ← MetricsRepository: time-series snapshots
│   └── event_log_repo.py             ← EventLogRepository: full audit trail
│
├── scheduler/                        ← Background job runner
│   ├── __init__.py
│   ├── scheduler.py                  ← BackgroundScheduler class
│   └── jobs.py                       ← All registered job definitions
│
├── monitoring/                       ← Observability layer
│   ├── __init__.py
│   ├── metrics.py                    ← MetricsCollector: counters, gauges, histograms
│   ├── health.py                     ← HealthChecker: per-service liveness
│   └── alerts.py                     ← AlertManager: threshold → ALERT_GENERATED
│
├── api/                              ← V2 FastAPI routes (prefix /api/v2)
│   ├── __init__.py
│   ├── router.py                     ← APIRouter with all V2 endpoints
│   ├── websocket.py                  ← /ws/v2/feed WebSocket endpoint
│   ├── auth.py                       ← Reuses V1 X-API-Key middleware
│   └── schemas.py                    ← Request / response Pydantic models
│
└── tests/                            ← V2 unit + integration tests
    ├── __init__.py
    ├── conftest.py                   ← pytest fixtures: in-memory DB, test bus
    ├── test_event_bus.py
    ├── test_risk_service.py
    ├── test_portfolio_service.py
    ├── test_scanner_service.py
    ├── test_repository.py
    └── test_websocket.py
```

**What lives at the project root (unchanged):**
```
app.py                                ← V1 FastAPI app (FROZEN — do not modify)
bots/                                 ← V1 bots (FROZEN)
monitoring/                           ← V1 monitoring (FROZEN)
v2/                                   ← V2 lives here entirely
```

**V2 entry point — standalone process (zero V1 file changes ever):**

V2 runs its own FastAPI application on a **separate port (5001)** as a second uvicorn process. This means:
- `app.py` (V1) is never modified — not even a single `include_router` line
- V2 routes are served from `v2/app_v2.py` (new file, not V1)
- The dashboard WebSocket client connects to the V2 port directly
- During V2.7 cut-over, `app.py` is *replaced* by `v2/app_v2.py` — the replacement is a deletion + rename, not an edit of V1 code

```
Port 5000  →  app.py         (V1 — frozen, read-only forever until retirement)
Port 5001  →  v2/app_v2.py   (V2 — all new endpoints, WebSocket feed)
```

The `v2/api/router.py` is mounted in `v2/app_v2.py`, never in `app.py`.

---

## 3. Shared Core

**Location**: `v2/core/`

The Core contains zero business logic. It is imported by all other V2 modules. Nothing else in V2 is imported by Core.

### 3.1 `core/types.py` — Domain Types

All V2 services share a single definition of each domain concept. V1 has scattered dataclasses; V2 defines them once.

```
Signal
  id:              str          (uuid4)
  coin:            str          e.g. "BTC"
  pair:            str          e.g. "B-BTC_USDT"
  market_state:    MarketState  (enum: breakout|bull_trend|pullback|recovery|downtrend|sideways)
  opportunity_type: OppType     (enum: momentum_trade|continuation|accumulation|…)
  priority:        Priority     (enum: Elite|High|Medium|Watch|Ignore)
  risk_level:      RiskLevel    (enum: low|medium|high)
  score:           int          (0–100, maps to V1 opportunity_score)
  confidence:      int          (0–100)
  coin_class:      str          ("A"|"B"|"C")
  mtf_alignment:   bool
  generated_at:    datetime     (UTC)
  expires_at:      datetime     (UTC, generated_at + TTL)
  source_bot:      str          ("scanner_v1")
  raw_payload:     dict         (original V1 response, for traceability)

Position
  id:              str          (uuid4)
  bot:             BotName      (enum: MTB|PMB|VGX)
  coin:            str
  pair:            str
  qty:             float
  entry_price:     float
  entry_time:      datetime
  current_price:   float        (updated on POSITION_UPDATED)
  unrealised_pnl:  float        (computed)
  stop_loss:       float | None
  take_profit:     float | None
  mode:            BotMode      (enum: PAPER|LIVE)
  signal_id:       str | None   (FK to Signal that triggered entry)
  status:          PositionStatus (enum: OPEN|CLOSING|CLOSED)

Trade
  id:              str          (uuid4)
  position_id:     str          (FK to Position)
  bot:             BotName
  coin:            str
  entry_price:     float
  exit_price:      float
  qty:             float
  pnl:             float
  pnl_pct:         float
  entry_time:      datetime
  exit_time:       datetime
  exit_reason:     ExitReason   (enum: TAKE_PROFIT|STOP_LOSS|MANUAL|CIRCUIT_BREAKER)
  mode:            BotMode
  signal_id:       str | None

BotSnapshot
  bot:             BotName
  mode:            BotMode
  status:          BotStatus    (enum: RUNNING|PAUSED|DISABLED|ERROR)
  cash_balance:    float
  deployed_capital: float
  open_positions:  int
  total_pnl:       float
  last_cycle_at:   datetime
  health_score:    int          (0–100)
  captured_at:     datetime
```

### 3.2 `core/config.py` — V2Config

Single Pydantic `BaseSettings` model. All env vars for V2 are `V2_`-prefixed to avoid collision with V1.

```
V2Config
  # Database
  v2_db_path:              str     default="v2/data/alpha_v2.db"
  
  # Capital limits (inherit from V1 env vars during transition)
  total_capital_limit:     float   (reads TOTAL_CAPITAL_LIMIT)
  mtb_capital_limit:       float   (reads MTB_CAPITAL_LIMIT)
  pmb_capital_limit:       float   (reads PMB_CAPITAL_LIMIT)
  vgx_capital_limit:       float   (reads VGX_CAPITAL_LIMIT)
  
  # Scanner
  v2_scanner_poll_interval: int    default=60   seconds
  v2_scanner_signal_ttl:    int    default=300  seconds (5 min)
  v2_scanner_base_url:      str    default="http://localhost:5000/api/v1/scanner"
  
  # WebSocket
  v2_ws_heartbeat_interval: int    default=15   seconds
  v2_ws_max_connections:    int    default=50
  
  # Scheduler
  v2_metrics_snapshot_interval: int  default=60   seconds
  v2_health_check_interval:     int  default=30   seconds
  v2_event_log_retention_days:  int  default=30
  
  # Notification
  alert_bot_token:          str | None  (reads ALERT_BOT_TOKEN)
  alert_chat_id:            str | None
  
  # Auth (shared with V1)
  dashboard_api_key:        str    (reads DASHBOARD_API_KEY)
  
  # Feature flags (all off by default)
  v2_websocket_enabled:     bool   default=False
  v2_shadow_mode:           bool   default=False  (V2 observes, does not trade)
  v2_trading_enabled:       bool   default=False  (gates V2 execution path)
```

### 3.3 `core/exceptions.py` — Exception Hierarchy

```
V2Error                        ← base
├── ConfigError                ← missing/invalid config
├── StorageError               ← DB or repo failure
│   └── MigrationError         ← schema migration failed
├── ServiceError               ← business logic failure
│   ├── RiskDenied             ← trade blocked by risk engine
│   └── SignalExpired          ← signal past TTL
├── SchedulerError             ← job registration / execution failure
└── BusError                   ← event publish / subscribe failure
```

### 3.4 `core/logging.py` — Structured Logger

Returns a `logging.Logger` configured with JSON formatting, service name, and correlation ID support. Every V2 log line is parseable as JSON — forward-compatible with Loki, Datadog, CloudWatch.

---

## 4. Event Bus — Full Definition

**Location**: `v2/bus/`  
**Existing**: `event_bus.py` (functional), `event_types.py` (16 events, expand to 28)

### 4.1 Expanded Event Types

The existing 16 events are kept unchanged. 12 new events are added:

```python
class EventType(str, Enum):

    # ── Signal lifecycle (existing) ─────────────────────────────────────────
    SIGNAL_GENERATED          = "signal.generated"
    SIGNAL_UPDATED            = "signal.updated"
    SIGNAL_EXPIRED            = "signal.expired"

    # ── Position lifecycle (existing) ───────────────────────────────────────
    POSITION_OPENED           = "position.opened"
    POSITION_CLOSED           = "position.closed"
    POSITION_UPDATED          = "position.updated"

    # ── Risk / circuit-breaker (existing) ───────────────────────────────────
    CAPITAL_LIMIT_HIT         = "risk.capital_limit_hit"
    DRAWDOWN_LIMIT_HIT        = "risk.drawdown_limit_hit"
    CIRCUIT_BREAKER_TRIGGERED = "risk.circuit_breaker_triggered"

    # ── Bot lifecycle (existing) ────────────────────────────────────────────
    BOT_STARTED               = "bot.started"
    BOT_STOPPED               = "bot.stopped"
    BOT_ERROR                 = "bot.error"

    # ── Portfolio / metrics (existing) ──────────────────────────────────────
    METRICS_UPDATED           = "metrics.updated"
    PORTFOLIO_UPDATED         = "portfolio.updated"
    ALERT_GENERATED           = "alert.generated"

    # ── NEW: Trade lifecycle ─────────────────────────────────────────────────
    TRADE_APPROVED            = "trade.approved"       # risk check passed
    TRADE_DENIED              = "trade.denied"         # risk check failed
    TRADE_EXECUTED            = "trade.executed"       # order placed
    TRADE_CLOSED              = "trade.closed"         # position exited

    # ── NEW: Scheduler ───────────────────────────────────────────────────────
    JOB_STARTED               = "scheduler.job_started"
    JOB_COMPLETED             = "scheduler.job_completed"
    JOB_FAILED                = "scheduler.job_failed"

    # ── NEW: System ──────────────────────────────────────────────────────────
    SYSTEM_STARTUP            = "system.startup"
    SYSTEM_SHUTDOWN           = "system.shutdown"
    HEALTH_DEGRADED           = "system.health_degraded"
    HEALTH_RECOVERED          = "system.health_recovered"

    # ── NEW: Configuration ───────────────────────────────────────────────────
    TRADING_ENABLED           = "config.trading_enabled"
    TRADING_DISABLED          = "config.trading_disabled"
    EMERGENCY_STOP_TRIGGERED  = "config.emergency_stop"
```

### 4.2 Event Payload Contracts

Every event carries a typed payload dict. These are contracts, not schemas — they are enforced by the publishing service.

| Event | Required Payload Keys |
|---|---|
| `SIGNAL_GENERATED` | `signal_id`, `coin`, `priority`, `score`, `market_state`, `expires_at` |
| `SIGNAL_EXPIRED` | `signal_id`, `coin`, `reason` |
| `POSITION_OPENED` | `position_id`, `bot`, `coin`, `qty`, `entry_price`, `mode` |
| `POSITION_CLOSED` | `position_id`, `bot`, `coin`, `exit_price`, `pnl`, `exit_reason` |
| `TRADE_APPROVED` | `bot`, `coin`, `amount`, `signal_id`, `check_ms` |
| `TRADE_DENIED` | `bot`, `coin`, `amount`, `code`, `reason` |
| `CAPITAL_LIMIT_HIT` | `bot`, `current_deployed`, `limit`, `requested_amount` |
| `CIRCUIT_BREAKER_TRIGGERED` | `trigger_event`, `affected_bots`, `triggered_at` |
| `BOT_ERROR` | `bot`, `error_type`, `message`, `traceback` |
| `METRICS_UPDATED` | `snapshot_id`, `captured_at`, `per_bot` (dict) |
| `PORTFOLIO_UPDATED` | `total_aum`, `total_deployed`, `total_cash`, `total_pnl` |
| `ALERT_GENERATED` | `level` (INFO/WARN/CRITICAL), `title`, `body`, `event_ref` |
| `HEALTH_DEGRADED` | `service`, `score`, `reason` |
| `TRADING_ENABLED` | `triggered_by`, `timestamp` |
| `EMERGENCY_STOP_TRIGGERED` | `triggered_by`, `timestamp`, `affected_bots` |

### 4.3 Bus Topology — Who Publishes / Subscribes What

```
Publisher                  Event                         Subscribers
─────────────────────────────────────────────────────────────────────────────
ScannerService          →  SIGNAL_GENERATED          →  RiskService
                                                         SignalRepository (persist)
                                                         DashboardService (push WS)

                        →  SIGNAL_EXPIRED            →  SignalRepository (update)
                                                         DashboardService (push WS)

RiskService             →  TRADE_APPROVED            →  TradingService
                        →  TRADE_DENIED              →  AlertManager (→ ALERT_GENERATED)
                                                         EventLogRepository
                        →  CAPITAL_LIMIT_HIT         →  AlertManager (→ ALERT_GENERATED)
                                                         DashboardService (push WS)
                        →  CIRCUIT_BREAKER_TRIGGERED →  TradingService (halt)
                                                         AlertManager (→ ALERT_GENERATED, CRITICAL)
                                                         DashboardService (push WS)

TradingService          →  POSITION_OPENED           →  PortfolioService
                                                         PositionRepository
                                                         DashboardService (push WS)
                        →  POSITION_CLOSED           →  PortfolioService
                                                         TradeRepository
                                                         MetricsRepository
                                                         DashboardService (push WS)
                        →  TRADE_EXECUTED            →  EventLogRepository

PortfolioService        →  PORTFOLIO_UPDATED         →  DashboardService (push WS)
                                                         MetricsRepository (snapshot)

BackgroundScheduler     →  METRICS_UPDATED           →  DashboardService (push WS)
                                                         MetricsRepository
                        →  JOB_FAILED                →  NotificationService

MonitoringService       →  ALERT_GENERATED           →  NotificationService
                        →  HEALTH_DEGRADED           →  NotificationService
                                                         DashboardService (push WS)

app.py (V1 bridge)      →  TRADING_ENABLED/DISABLED  →  RiskService (sync state)
                        →  EMERGENCY_STOP_TRIGGERED  →  RiskService + TradingService
```

### 4.4 Bus Guarantees and Limitations

| Guarantee | Detail |
|---|---|
| **Non-blocking** | One failing handler never blocks others (`asyncio.gather`, exceptions caught) |
| **In-process** | Single asyncio event loop — no network hop, no broker needed in V2.0–V2.6 |
| **No persistence** | Events are fire-and-forget on the bus; persistence is the Repository's job |
| **Ordering** | Events are processed in the order `publish()` is called within a single task |
| **Concurrency** | All handlers for one event run concurrently; handlers for different events may interleave |

**Upgrade path**: If V2.7+ needs cross-process or cross-host messaging, replace `EventBus` with a Redis Streams adapter behind the same `publish`/`subscribe` interface. No service code changes.

---

## 5. Service Layer

**Location**: `v2/services/`

Each service is a class with a well-defined interface:
- `start() → None` — called once at application startup; subscribes handlers to bus
- `stop() → None` — called on shutdown; unsubscribes, flushes pending work
- Services communicate exclusively via the Event Bus and Repository layer
- Services never import from other services

### 5.1 ScannerService

**Purpose**: Bridge between V1 scanner and V2 event system.

**Responsibilities:**
- Poll `GET /api/v1/scanner/signals` every `V2_SCANNER_POLL_INTERVAL` seconds
- Transform V1 HTTP response → V2 `Signal` domain type via `adapter.py`
- Compute signal `expires_at` from `generated_at + V2_SCANNER_SIGNAL_TTL`
- Deduplicate: only publish `SIGNAL_GENERATED` for signals not yet seen (keyed by coin + generated_at)
- Detect expiry: scan in-memory live set and publish `SIGNAL_EXPIRED` for TTL-elapsed entries
- Publish `SIGNAL_UPDATED` when a signal's score changes on re-poll
- Write signals to `SignalRepository`
- Expose `get_live_signals() → list[Signal]` for DashboardService

**Does NOT:**
- Parse market data directly (that is V1 scanner's job)
- Make trading decisions

**V1 coupling**: HTTP call to `localhost:5000` only. Cut over in V2.7 by pointing at V2 scanner internals.

### 5.2 RiskService

**Purpose**: Enforce all capital and safety limits. Single gatekeeper for V2 trades.

**Responsibilities:**
- Subscribe to `SIGNAL_GENERATED` — pre-evaluate each signal against current capital state and publish `TRADE_APPROVED` or `TRADE_DENIED`
- Expose `check_trade_allowed(bot: BotName, amount: float) → RiskDecision`
- `CapitalGuard`: reads deployed capital from `PositionRepository` (not bot storage modules)
- `CircuitBreaker`: tracks consecutive losses and drawdown; triggers `CIRCUIT_BREAKER_TRIGGERED` when threshold exceeded
- Subscribe to `POSITION_OPENED` / `POSITION_CLOSED` to maintain live deployed-capital cache (in-memory, backed by repository)
- Subscribe to `POSITION_CLOSED` to update `daily_pnl` per bot — this is the drawdown and circuit-breaker trigger input
- Subscribe to `TRADING_ENABLED` / `TRADING_DISABLED` / `EMERGENCY_STOP_TRIGGERED` to sync global kill switches
- Publish `TRADE_APPROVED`, `TRADE_DENIED`, `CAPITAL_LIMIT_HIT`, `DRAWDOWN_LIMIT_HIT`, `CIRCUIT_BREAKER_TRIGGERED`

**State model:**
```
RiskState
  trading_enabled:      bool
  emergency_stop:       bool
  circuit_breaker_open: bool
  per_bot_deployed:     dict[BotName, float]   ← maintained from POSITION_* events
  daily_pnl:            dict[BotName, float]   ← maintained from POSITION_CLOSED events (pnl field)
  last_capital_check:   datetime
```

**Does NOT:**
- Import bot storage modules (V1 coupling broken)
- Make position or trade decisions

### 5.3 PortfolioService

**Purpose**: Single source of truth for aggregate portfolio state.

**Responsibilities:**
- Subscribe to `POSITION_OPENED`, `POSITION_CLOSED`, `POSITION_UPDATED`
- Maintain in-memory `PortfolioState`: total AUM, per-bot deployed/cash/PnL
- Recompute `total_deployed`, `total_cash`, `total_pnl` on every position event
- Publish `PORTFOLIO_UPDATED` after each recompute
- Expose `get_snapshot() → PortfolioSnapshot` for API and DashboardService
- Persist periodic snapshots to `MetricsRepository`

**PortfolioSnapshot fields:**
```
total_aum:             float
total_deployed:        float
total_cash:            float
total_unrealised_pnl:  float
total_realised_pnl:    float
daily_pnl:             float
positions_by_bot:      dict[BotName, list[Position]]
capital_utilisation:   float   (total_deployed / total_aum × 100)
captured_at:           datetime
```

### 5.4 TradingService

**Purpose**: Signal-to-execution pipeline. Orchestrates the full trade lifecycle.

**Responsibilities:**
- Subscribe to `TRADE_APPROVED` events (from RiskService) — this is the ONLY entry point; TradingService does NOT subscribe to SIGNAL_GENERATED
- Route approved signals to the correct bot adapter (MTB / PMB / VGX)
- Adapters call V1 bot HTTP endpoints during transition; replaced with direct V2 execution in V2.7
- Publish `TRADE_EXECUTED`, `POSITION_OPENED`
- Subscribe to bot exit signals (V1 polling during transition) → publish `POSITION_CLOSED`
- Honour `CIRCUIT_BREAKER_TRIGGERED` by halting all pending executions

**Bot Adapters (transition period — V2.1–V2.6):**
```
MTBAdapter.execute(signal: Signal) → Position
  POST /api/v1/mtb/execute  (V1 internal endpoint — added during V2.1)

PMBAdapter.execute(signal: Signal) → Position
  POST /api/v1/pmb/execute

VGXAdapter.execute(signal: Signal) → Position
  POST /api/v1/vgx/execute
```

**V2.7+**: Adapters call V2 trading engine directly. HTTP adapters retired.

### 5.5 DashboardService

**Purpose**: Aggregate and serve real-time state to the dashboard.

**Responsibilities:**
- Build `DashboardSnapshot` (equivalent of V1 `/api/v1/state`) from:
  - `PortfolioService.get_snapshot()`
  - `ScannerService.get_live_signals()`
  - `RiskService.get_state()`
  - `MetricsRepository.get_latest_snapshot()`
- Push snapshot delta to all connected WebSocket clients on any relevant event
- Expose `/api/v2/state` REST endpoint (backward-compatible superset of V1 `/api/v1/state`)
- Manage `WebSocketConnectionManager` (see §10)

**Subscribes to** (triggers a WS push):
```
SIGNAL_GENERATED, SIGNAL_EXPIRED
POSITION_OPENED, POSITION_CLOSED, POSITION_UPDATED
PORTFOLIO_UPDATED
CAPITAL_LIMIT_HIT, CIRCUIT_BREAKER_TRIGGERED
METRICS_UPDATED
HEALTH_DEGRADED, HEALTH_RECOVERED
TRADING_ENABLED, TRADING_DISABLED
```

### 5.6 NotificationService

**Purpose**: Unified alert dispatch (replaces 4× V1 Telegram bots).

**Responsibilities:**
- Subscribe exclusively to `ALERT_GENERATED` — NotificationService never subscribes to raw risk or trading events directly; those go through AlertManager first
- Route by `level`: INFO → optional, WARN → Telegram, CRITICAL → Telegram + log
- Format messages using per-event templates from `formatters.py`
- Deduplicate: suppress identical alerts within a cooldown window (default 5 min)
- Rate limit: max 10 messages per minute per channel
- Expose `notify(level, title, body) → None` for other services to use directly (publishes `ALERT_GENERATED`)

**Channels supported (V2.0–V2.4):**
- Telegram (single bot, single `ALERT_BOT_TOKEN`, multiple `CHAT_ID` routing by level)

**Upgrade path** (V2.5+): Add webhook, email, PagerDuty behind the same `notify()` interface.

---

## 6. Repository Layer

**Location**: `v2/repository/`

Repositories are the only code that touches the database. Services never write SQL. The Repository returns domain types (defined in `core/types.py`), not raw dicts.

### 6.1 `base.py` — BaseRepository

```
BaseRepository (ABC)
  db: DatabaseConnection   ← injected at construction

  Methods (all async):
    _execute(sql, params) → sqlite3.Row
    _fetchone(sql, params) → Row | None
    _fetchall(sql, params) → list[Row]
    _fetchmany(sql, params, limit) → list[Row]
```

### 6.2 `db.py` — Database Connection

- Opens a single SQLite connection with `PRAGMA journal_mode=WAL` (Write-Ahead Logging — allows concurrent reads)
- Runs schema migrations on startup (versioned, sequential SQL files)
- Exposes `get_connection() → sqlite3.Connection` for repositories
- WAL mode ensures reads do not block writes — important because the dashboard reads while bots write

### 6.3 `position_repo.py` — PositionRepository

```
PositionRepository
  insert(position: Position) → str                    position_id
  update_price(id, price, unrealised_pnl) → None
  close(id, exit_price, exit_reason) → None
  get_by_id(id) → Position | None
  get_open(bot: BotName | None) → list[Position]
  get_deployed_capital(bot: BotName) → float
  get_all_deployed_capital() → dict[BotName, float]
```

### 6.4 `trade_repo.py` — TradeRepository

```
TradeRepository
  insert(trade: Trade) → str                          trade_id
  get_by_id(id) → Trade | None
  get_by_bot(bot, limit, offset) → list[Trade]
  get_by_coin(coin, limit) → list[Trade]
  get_since(since: datetime) → list[Trade]
  get_win_rate(bot: BotName | None, since: datetime) → float
  get_pnl_series(bot: BotName | None, since: datetime) → list[tuple[datetime, float]]
```

### 6.5 `signal_repo.py` — SignalRepository

```
SignalRepository
  insert(signal: Signal) → str                        signal_id
  mark_expired(id, reason) → None
  get_live(priority_gte: Priority | None) → list[Signal]
  get_by_coin(coin, limit) → list[Signal]
  get_history(since: datetime, limit) → list[Signal]
  count_by_priority(since: datetime) → dict[Priority, int]
```

### 6.6 `metrics_repo.py` — MetricsRepository

```
MetricsRepository
  insert_snapshot(snapshot: MetricsSnapshot) → str    snapshot_id
  get_latest() → MetricsSnapshot | None
  get_series(metric: str, since: datetime) → list[tuple[datetime, float]]
  get_bot_health_series(bot: BotName, since: datetime) → list[tuple[datetime, int]]
```

### 6.7 `event_log_repo.py` — EventLogRepository

Immutable append-only table. Every event published on the bus is logged here automatically by a universal subscriber wired in `subscribers.py`.

```
EventLogRepository
  append(event_type, payload, source_service) → str   entry_id
  get_since(since: datetime, limit) → list[EventLogEntry]
  get_by_type(event_type, limit) → list[EventLogEntry]
  get_by_entity(entity_id: str) → list[EventLogEntry]  (position_id, signal_id, etc.)
  prune_before(cutoff: datetime) → int                  rows deleted
```

---

## 7. SQLite Schema

**File**: `v2/data/alpha_v2.db`  
**Engine**: SQLite 3 with WAL journal mode  
**Migrations**: Sequential numbered SQL files applied at startup

### Migration 001 — Core Tables

```sql
-- ── Schema version tracking ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL,            -- ISO8601 UTC
    description TEXT NOT NULL
);

-- ── Signals ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS signals (
    id               TEXT PRIMARY KEY,    -- uuid4
    coin             TEXT NOT NULL,
    pair             TEXT NOT NULL,
    market_state     TEXT NOT NULL,
    opportunity_type TEXT NOT NULL,
    priority         TEXT NOT NULL,       -- Elite|High|Medium|Watch|Ignore
    risk_level       TEXT NOT NULL,       -- low|medium|high
    score            INTEGER NOT NULL,    -- 0-100
    confidence       INTEGER NOT NULL,    -- 0-100
    coin_class       TEXT,                -- A|B|C
    mtf_alignment    INTEGER NOT NULL DEFAULT 0,   -- 0|1
    generated_at     TEXT NOT NULL,       -- ISO8601 UTC
    expires_at       TEXT NOT NULL,       -- ISO8601 UTC
    expired_at       TEXT,                -- NULL = still live
    expiry_reason    TEXT,                -- TTL|OVERRIDE|MANUAL
    source_bot       TEXT NOT NULL DEFAULT 'scanner_v1',
    raw_payload      TEXT                 -- JSON blob
);

CREATE INDEX IF NOT EXISTS idx_signals_priority      ON signals (priority);
CREATE INDEX IF NOT EXISTS idx_signals_coin          ON signals (coin);
CREATE INDEX IF NOT EXISTS idx_signals_generated_at  ON signals (generated_at DESC);
CREATE INDEX IF NOT EXISTS idx_signals_expires_at    ON signals (expires_at);

-- ── Positions ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS positions (
    id               TEXT PRIMARY KEY,    -- uuid4
    bot              TEXT NOT NULL,       -- MTB|PMB|VGX
    coin             TEXT NOT NULL,
    pair             TEXT NOT NULL,
    qty              REAL NOT NULL,
    entry_price      REAL NOT NULL,
    entry_time       TEXT NOT NULL,       -- ISO8601 UTC
    current_price    REAL,
    unrealised_pnl   REAL,
    stop_loss        REAL,
    take_profit      REAL,
    mode             TEXT NOT NULL,       -- PAPER|LIVE
    signal_id        TEXT,                -- FK signals(id), nullable
    status           TEXT NOT NULL DEFAULT 'OPEN',  -- OPEN|CLOSING|CLOSED
    exit_price       REAL,                -- NULL until closed; set by PositionRepository.close()
    exit_reason      TEXT,                -- NULL until closed; TAKE_PROFIT|STOP_LOSS|MANUAL|CIRCUIT_BREAKER
    closed_at        TEXT,                -- NULL until closed; ISO8601 UTC
    FOREIGN KEY (signal_id) REFERENCES signals (id)
);

-- NOTE: exit_price / exit_reason / closed_at are written atomically by
-- PositionRepository.close(id, exit_price, exit_reason) in a single UPDATE.
-- TradeRepository.insert() is called immediately after to create the closed-trade record.

CREATE INDEX IF NOT EXISTS idx_positions_bot         ON positions (bot);
CREATE INDEX IF NOT EXISTS idx_positions_status      ON positions (status);
CREATE INDEX IF NOT EXISTS idx_positions_coin        ON positions (coin);

-- ── Trades (closed positions) ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS trades (
    id               TEXT PRIMARY KEY,    -- uuid4
    position_id      TEXT NOT NULL,       -- FK positions(id)
    bot              TEXT NOT NULL,
    coin             TEXT NOT NULL,
    pair             TEXT NOT NULL,
    entry_price      REAL NOT NULL,
    exit_price       REAL NOT NULL,
    qty              REAL NOT NULL,
    pnl              REAL NOT NULL,
    pnl_pct          REAL NOT NULL,
    entry_time       TEXT NOT NULL,
    exit_time        TEXT NOT NULL,
    exit_reason      TEXT NOT NULL,       -- TAKE_PROFIT|STOP_LOSS|MANUAL|CIRCUIT_BREAKER
    mode             TEXT NOT NULL,
    signal_id        TEXT,
    FOREIGN KEY (position_id) REFERENCES positions (id),
    FOREIGN KEY (signal_id)   REFERENCES signals   (id)
);

CREATE INDEX IF NOT EXISTS idx_trades_bot            ON trades (bot);
CREATE INDEX IF NOT EXISTS idx_trades_exit_time      ON trades (exit_time DESC);
CREATE INDEX IF NOT EXISTS idx_trades_coin           ON trades (coin);
CREATE INDEX IF NOT EXISTS idx_trades_exit_reason    ON trades (exit_reason);

-- ── Metrics snapshots ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS metrics_snapshots (
    id               TEXT PRIMARY KEY,    -- uuid4
    captured_at      TEXT NOT NULL,       -- ISO8601 UTC

    -- Portfolio aggregate
    total_aum        REAL,
    total_deployed   REAL,
    total_cash       REAL,
    total_unrealised REAL,
    total_realised   REAL,
    daily_pnl        REAL,
    capital_util_pct REAL,

    -- Per-bot JSON (serialised BotSnapshot list)
    per_bot_json     TEXT
);

CREATE INDEX IF NOT EXISTS idx_metrics_captured_at   ON metrics_snapshots (captured_at DESC);

-- ── Bot state snapshots ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS bot_snapshots (
    id               TEXT PRIMARY KEY,    -- uuid4
    bot              TEXT NOT NULL,
    mode             TEXT NOT NULL,
    status           TEXT NOT NULL,
    cash_balance     REAL,
    deployed_capital REAL,
    open_positions   INTEGER,
    total_pnl        REAL,
    health_score     INTEGER,
    captured_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bot_snapshots_bot     ON bot_snapshots (bot, captured_at DESC);

-- ── Event log (append-only audit trail) ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS event_log (
    id               TEXT PRIMARY KEY,    -- uuid4
    event_type       TEXT NOT NULL,       -- EventType value
    source_service   TEXT,
    entity_id        TEXT,                -- position_id, signal_id, etc.
    payload_json     TEXT NOT NULL,       -- full event payload
    logged_at        TEXT NOT NULL        -- ISO8601 UTC
);

CREATE INDEX IF NOT EXISTS idx_event_log_type        ON event_log (event_type);
CREATE INDEX IF NOT EXISTS idx_event_log_entity      ON event_log (entity_id);
CREATE INDEX IF NOT EXISTS idx_event_log_logged_at   ON event_log (logged_at DESC);
```

### Schema Design Notes

| Decision | Rationale |
|---|---|
| `TEXT` for all datetimes | SQLite has no native datetime type; ISO8601 strings sort lexicographically, enabling `ORDER BY logged_at` without casting |
| `TEXT` for IDs | UUID4 strings — portable, no auto-increment collision risk across parallel processes |
| `FOREIGN KEY` on `signal_id` nullable | Not every position comes from a scanner signal (manual / VGX grid entries) |
| `event_log` append-only | No UPDATE or DELETE on this table; pruning happens only via `prune_before()` on a scheduled job |
| WAL mode | Allows simultaneous readers while one writer is active — critical for dashboard reads during bot writes |
| `per_bot_json` in metrics | Avoids a complex join for what is fundamentally a time-series snapshot; analytics queries use the snapshot as an atomic unit |

---

## 8. Configuration System

**Location**: `v2/core/config.py`

### Design

```
┌─────────────────────────────────────────┐
│              V2Config                   │
│  (Pydantic BaseSettings)                │
│                                         │
│  1. Reads environment variables         │
│  2. Reads v2/data/config_override.json  │
│     (optional, runtime hot-reload)      │
│  3. Falls back to coded defaults        │
└─────────────────────────────────────────┘
           │
           │  Injected at startup into:
           ▼
   ScannerService
   RiskService
   PortfolioService
   TradingService
   DashboardService
   NotificationService
   BackgroundScheduler
   MonitoringService
```

### Rules
1. **No service calls `os.getenv()` directly.** All config access is through the injected `V2Config` instance.
2. **V1 environment variables are read once** (at startup) and stored in `V2Config` fields. V2 never re-reads V1 vars at runtime.
3. **Runtime overrides** (`config_override.json`) are hot-reloaded by the `CONFIG_RELOAD` scheduler job (every 60s). Only a subset of keys are hot-reloadable (feature flags, alert thresholds). Capital limits require a restart.
4. **Feature flags** control the V1→V2 cut-over. Setting `V2_TRADING_ENABLED=true` moves trading decisions from V1 to V2. Setting `V2_SHADOW_MODE=true` runs V2 logic alongside V1 but does not execute orders.

### Hot-Reloadable Keys
```
v2_websocket_enabled
v2_shadow_mode
v2_trading_enabled
v2_scanner_poll_interval
v2_scanner_signal_ttl
v2_metrics_snapshot_interval
v2_health_check_interval
alert_bot_token
alert_chat_id
```

---

## 9. Background Scheduler

**Location**: `v2/scheduler/`

### Design

The BackgroundScheduler runs as an asyncio background task. It maintains a registry of named jobs, each with an interval, last-run time, and enabled/disabled flag.

```
BackgroundScheduler
  _jobs: dict[str, JobDefinition]
  _running: bool

  register(name, coroutine_fn, interval_seconds, enabled=True) → None
  enable(name) → None
  disable(name) → None
  start() → None          (creates asyncio task)
  stop() → None
  get_status() → list[JobStatus]
```

```
JobDefinition
  name:             str
  fn:               Coroutine
  interval:         int          seconds
  enabled:          bool
  last_run_at:      datetime | None
  last_duration_ms: int | None
  last_error:       str | None
  run_count:        int
  error_count:      int
```

The scheduler publishes `JOB_STARTED`, `JOB_COMPLETED`, and `JOB_FAILED` events so the dashboard and notification service react without polling.

### Registered Jobs

| Job Name | Interval | Description |
|---|---|---|
| `scanner_poll` | `V2_SCANNER_POLL_INTERVAL` (60s) | ScannerService polls V1 API, publishes `SIGNAL_*` |
| `signal_expiry_check` | 30s | Scan in-memory live signals, publish `SIGNAL_EXPIRED` for stale |
| `metrics_snapshot` | `V2_METRICS_SNAPSHOT_INTERVAL` (60s) | Capture portfolio + bot state, write to `metrics_snapshots` |
| `bot_health_check` | `V2_HEALTH_CHECK_INTERVAL` (30s) | Poll each bot's health endpoint, publish `HEALTH_DEGRADED/RECOVERED` |
| `risk_state_sync` | 30s | Re-read V1 `TRADING_ENABLED` / `EMERGENCY_STOP` state; publish events if changed |
| `event_log_prune` | 3600s (1h) | Delete `event_log` rows older than `V2_EVENT_LOG_RETENTION_DAYS` |
| `config_reload` | 60s | Hot-reload `config_override.json` |
| `ws_heartbeat` | `V2_WS_HEARTBEAT_INTERVAL` (15s) | Ping all WebSocket clients; drop stale connections |

### Failure Policy

- A job that raises an exception has its error logged and `JOB_FAILED` published. It does **not** crash the scheduler.
- After 5 consecutive failures, the job is auto-disabled and `ALERT_GENERATED` with level=WARN is published.
- The scheduler itself runs in a `try/except` loop — a crash in scheduler infrastructure publishes `BOT_ERROR` and restarts after 5s.

---

## 10. WebSocket Push Feed

**Location**: `v2/services/dashboard_service/websocket.py`, `v2/api/websocket.py`

### Endpoint

```
GET /ws/v2/feed
Headers: X-API-Key: <DASHBOARD_API_KEY>   (same key as V1 REST API)
Protocol: WebSocket
```

### Connection Manager

```
WebSocketConnectionManager
  _connections: dict[str, WebSocketConnection]   keyed by connection_id (uuid4)
  _subscriptions: dict[str, set[EventType]]      per-connection filter

  connect(ws, filters: list[EventType] | None) → str    connection_id
  disconnect(connection_id) → None
  send(connection_id, message: dict) → None             (catches stale/closed)
  broadcast(message: dict, event_type: EventType) → None
  ping_all() → None                                     (drops unresponsive)
  connection_count() → int
  get_status() → list[ConnectionStatus]
```

### Message Protocol

Every WebSocket message is a JSON object:

```json
{
  "v":   2,
  "id":  "uuid4",
  "ts":  "2026-07-07T17:00:00Z",
  "type": "signal.generated",
  "data": { ... event payload ... }
}
```

Client-side filtering: a client may send a subscription message after connecting:
```json
{ "action": "subscribe", "events": ["signal.generated", "portfolio.updated"] }
```
The server only pushes events matching the client's subscription list. If no subscription is set, all events are pushed.

### Heartbeat

Every `V2_WS_HEARTBEAT_INTERVAL` seconds the scheduler fires `ws_heartbeat`:
```json
{ "v": 2, "id": "...", "ts": "...", "type": "system.heartbeat", "data": {} }
```
Clients that do not respond to a ping within 10s are dropped and `disconnect()` is called.

### Dashboard Integration

The V1 dashboard JS (in `static/`) currently polls `/api/v1/state` every 3 seconds. During V2.5, the dashboard JS is upgraded to open `/ws/v2/feed` and apply incremental updates. The polling fallback is kept for browsers that cannot use WebSocket.

---

## 11. Monitoring / Metrics

**Location**: `v2/monitoring/`

### 11.1 MetricsCollector (`monitoring/metrics.py`)

Collects runtime counters and gauges in-memory. Exposed at `/api/v2/metrics` in Prometheus text format (forward-compatible with Grafana).

```
MetricsCollector
  Counters (increment-only):
    signals_generated_total       (labels: priority)
    trades_approved_total         (labels: bot)
    trades_denied_total           (labels: bot, code)
    positions_opened_total        (labels: bot, mode)
    positions_closed_total        (labels: bot, exit_reason)
    alerts_dispatched_total       (labels: level)
    ws_connections_total
    ws_messages_sent_total

  Gauges (set on event):
    open_positions_count          (labels: bot)
    deployed_capital_amount       (labels: bot)
    scanner_health_score
    signal_live_count
    ws_active_connections

  Histograms:
    risk_check_duration_ms        (labels: bot)
    scanner_poll_duration_ms
    db_write_duration_ms          (labels: table)
```

### 11.2 HealthChecker (`monitoring/health.py`)

Runs on `bot_health_check` scheduler job. Each service has a `health_check() → HealthStatus` method.

```
HealthStatus
  service:      str
  status:       str      (OK|DEGRADED|DOWN)
  score:        int      (0–100)
  details:      dict
  checked_at:   datetime
```

Per-service checks:
| Service | Check |
|---|---|
| `scanner_service` | Last poll ≤ 2× `SCANNER_POLL_INTERVAL` ago AND last score ≥ 80 |
| `risk_service` | State in sync with V1 runtime_state.json |
| `portfolio_service` | Last `PORTFOLIO_UPDATED` event ≤ 120s ago |
| `database` | Can execute `SELECT 1` in < 100ms |
| `event_bus` | Subscriber count ≥ minimum expected per event type |
| `scheduler` | No job in error_count > 5 |

`HEALTH_DEGRADED` / `HEALTH_RECOVERED` events are published when a service crosses the OK/DEGRADED boundary.

### 11.3 AlertManager (`monitoring/alerts.py`)

Threshold rules evaluated by `MonitoringService` (runs on `metrics_snapshot` job):

| Rule | Threshold | Alert Level |
|---|---|---|
| Capital utilisation | > 80% | WARN |
| Capital utilisation | > 95% | CRITICAL |
| Daily PnL drawdown | < −5% | WARN |
| Daily PnL drawdown | < −10% | CRITICAL |
| Scanner health score | < 70 | WARN |
| Scanner health score | < 50 | CRITICAL |
| Any bot in ERROR status | immediate | CRITICAL |
| Circuit breaker open | immediate | CRITICAL |
| Event log prune failed | next cycle | WARN |

AlertManager publishes `ALERT_GENERATED` which NotificationService handles. AlertManager itself does not send Telegram messages — that is strictly NotificationService's job.

---

## 12. Data Flow

### Flow A: Signal → Trade (V2 Shadow Mode)

```
V1 Scanner
  │
  │  HTTP GET /api/v1/scanner/signals
  │  (every 60s, scheduler job: scanner_poll)
  ▼
ScannerService.adapter.py
  │  V1 response → Signal domain type
  │  Deduplicate against known signal IDs
  ▼
SignalRepository.insert()
  │  Persist to SQLite signals table
  │
  ├─── publish(SIGNAL_GENERATED, {signal_id, coin, priority, score, …})
  │
  ▼
EventBus
  │
  ├──► RiskService.on_signal_generated()
  │      capital_guard.check(bot, estimated_amount)
  │      → if allowed: publish(TRADE_APPROVED)
  │      → if denied:  publish(TRADE_DENIED) → NotificationService
  │
  ├──► DashboardService.on_signal_generated()
  │      ws_manager.broadcast({type: "signal.generated", …})
  │
  └──► EventLogRepository.append()
            Audit trail entry
```

```
EventBus receives TRADE_APPROVED
  │
  ▼
TradingService.on_trade_approved()
  │
  │  [shadow mode]: log only, do not execute
  │  [trading mode]: call bot adapter
  │
  ▼
MTBAdapter.execute(signal) → Position
  │  POST /api/v1/mtb/execute (V2.1–2.6 transition)
  │
  ▼
PositionRepository.insert(position)
  │
  ├─── publish(POSITION_OPENED)
  │
  ▼
EventBus
  │
  ├──► PortfolioService.on_position_opened()
  │      Update in-memory state
  │      publish(PORTFOLIO_UPDATED)
  │
  ├──► RiskService.on_position_opened()
  │      Update deployed_capital cache
  │
  └──► DashboardService.on_position_opened()
             ws_manager.broadcast({type: "position.opened", …})
```

### Flow B: Position Exits

```
TradingService (polls V1 bot positions every 60s for closed positions)
  │  OR: V2.7 trading engine publishes directly
  │
  ├─── PositionRepository.close(id, exit_price, exit_reason)
  ├─── TradeRepository.insert(trade)
  └─── publish(POSITION_CLOSED)
  │
  ▼
EventBus
  │
  ├──► PortfolioService → PORTFOLIO_UPDATED
  ├──► MetricsRepository.insert_snapshot()
  ├──► DashboardService → WS push
  └──► NotificationService (if TAKE_PROFIT or STOP_LOSS → Telegram)
```

### Flow C: Dashboard WebSocket Client

```
Browser opens ws://host/ws/v2/feed
  │
  ▼
WebSocketConnectionManager.connect()
  │  Sends initial snapshot (current state)
  │
  ▼  (waiting for events)

Any relevant event published on bus
  │
  ▼
DashboardService.on_event()
  │
  ▼
ws_manager.broadcast(message, event_type)
  │
  ▼  (filtered by client subscription)
Browser receives JSON message → incremental UI update
```

### Flow D: Alert

```
MonitoringService (runs every metrics_snapshot)
  │  Capital utilisation > 80%
  │
  ▼
AlertManager.evaluate(snapshot)
  │
  └─── publish(ALERT_GENERATED, {level: "WARN", title: "…", body: "…"})
  │
  ▼
NotificationService.on_alert_generated()
  │  Dedup check (same alert within 5 min? → suppress)
  │
  ▼
TelegramDispatcher.send(chat_id, message)
```

---

## 13. Migration Roadmap V1 → V2

### Phase Table

| Phase | Label | What Changes | V1 State | V2 State |
|---|---|---|---|---|
| **V2.0** | ✅ Foundation | Event bus scaffold, service skeletons, folder structure | Full production | Architecture docs only |
| **V2.1** | Scanner Bridge | `ScannerService` wraps V1 scanner API; `SignalRepository` live; event log writing | Unchanged | Scanner events flowing |
| **V2.2** | Risk Layer | `RiskService` reads from `PositionRepository`; breaks V1 storage coupling | Unchanged | Capital guard V2-native |
| **V2.3** | Portfolio Layer | `PortfolioService` + `MetricsRepository`; background scheduler live | Unchanged | Portfolio events flowing |
| **V2.4** | Notification | `NotificationService` replaces 4× V1 Telegram bots | V1 Telegram disabled | Single unified dispatcher |
| **V2.5** | Dashboard V2 | `/api/v2/` routes live; WebSocket feed live; dashboard JS upgraded | V1 API still works | Push-based dashboard |
| **V2.6** | Shadow Mode | `V2_SHADOW_MODE=true`: V2 makes all decisions, compares with V1, logs divergence | Runs in parallel | Shadow mode verified |
| **V2.7** | Cut-Over | `V2_TRADING_ENABLED=true`: V2 executes trades; V1 bots set to DISABLED | Standby | Full production |
| **V2.8** | V1 Retirement | V1 bot directories archived; `app.py` replaced by V2 entrypoint | Archived | Clean V2 only |

### Phase V2.1 — Entry Criteria
- This architecture document approved by user
- No V1 files modified

### Phase V2.1 — Exit Criteria
- `ScannerService` running as scheduler job
- Signals appearing in `signals` SQLite table
- `SIGNAL_GENERATED` / `SIGNAL_EXPIRED` events flowing on bus
- `/api/v2/scanner/signals` endpoint returning data
- Zero changes to V1

### Dependency Graph

```
V2.0 (done)
  └── V2.1 (scanner)
        └── V2.2 (risk)
              └── V2.3 (portfolio)
                    ├── V2.4 (notification) ← independent once V2.3 done
                    └── V2.5 (dashboard)
                          └── V2.6 (shadow mode)
                                └── V2.7 (cut-over)
                                      └── V2.8 (retirement)
```

V2.4 (notification) and V2.5 (dashboard) can be built in parallel once V2.3 is complete.

### V1 Coupling Removal Schedule

| V1 Coupling | Removed In | Notes |
|---|---|---|
| Risk engine imports `pmb_bot.storage`, `mtb_bot.storage` | V2.2 | Replaced by `PositionRepository` |
| Dashboard polls bots via `asyncio.to_thread` | V2.5 | Replaced by WebSocket push |
| 4× separate Telegram bot tokens | V2.4 | Replaced by single `ALERT_BOT_TOKEN` |
| Per-bot `config.py` | V2.7 | Replaced by `V2Config` |
| Per-bot `data/*.json` files | V2.7 | Replaced by SQLite via Repository |
| `asyncio.sleep` bot loops | V2.7 | Replaced by BackgroundScheduler |

---

*Document version: 1.0 — Phase 1 Architecture Definition*  
*Created: 2026-07-07*  
*Next step: User approval → V2.1 implementation begins*
