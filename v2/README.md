# PROJECT-ALPHA V2 — Architecture Foundation

> **Status**: Scaffolding only. No production logic. No V1 imports.

---

## Overview

V2 is a ground-up redesign of PROJECT-ALPHA built around an **event-driven architecture**. Every major state change in the system is represented as a typed event published to a central bus. Services subscribe to the events they care about and react independently — no tight coupling, no shared globals.

V1 remains fully operational. V2 is additive scaffolding only.

---

## Directory Structure

```
v2/
├── bus/
│   ├── event_bus.py        ← Async pub/sub bus (publish/subscribe/unsubscribe)
│   ├── event_types.py      ← All EventType constants (enum)
│   └── subscribers.py      ← Central handler registry (register_all)
│
├── services/
│   ├── scanner_service/    ← Signal generation pipeline
│   ├── risk_service/       ← Circuit breaker, drawdown enforcement
│   ├── portfolio_service/  ← Position tracking, AUM, PnL
│   ├── dashboard_service/  ← Web API / frontend aggregation
│   └── notification_service/ ← Telegram, alerts, webhooks
│
├── storage/                ← Async storage adapters (future)
├── analytics/              ← Metrics, win-rate, drawdown analytics
├── tests/                  ← V2 unit + integration tests
└── README.md               ← This file
```

---

## Event Bus

### Importing

```python
from v2.bus import bus, EventType

# Subscribe
bus.subscribe(EventType.SIGNAL_GENERATED, my_handler)

# Publish
await bus.publish(EventType.SIGNAL_GENERATED, payload={"coin": "BTC", "score": 87})

# Unsubscribe
bus.unsubscribe(EventType.SIGNAL_GENERATED, my_handler)
```

### Handler signature

```python
async def my_handler(event_type: EventType, payload: dict) -> None:
    ...
```

---

## Event Types

| Category | Event | Description |
|---|---|---|
| Signal | `SIGNAL_GENERATED` | New signal created by scanner |
| Signal | `SIGNAL_UPDATED` | Score or metadata updated |
| Signal | `SIGNAL_EXPIRED` | Signal past TTL or overridden |
| Position | `POSITION_OPENED` | Trade entered |
| Position | `POSITION_CLOSED` | Trade exited (TP/SL/manual) |
| Position | `POSITION_UPDATED` | Trailing stop or DCA update |
| Risk | `CAPITAL_LIMIT_HIT` | Deployed capital at ceiling |
| Risk | `DRAWDOWN_LIMIT_HIT` | Daily drawdown threshold reached |
| Risk | `CIRCUIT_BREAKER_TRIGGERED` | All bots halted |
| Bot | `BOT_STARTED` | Bot process started |
| Bot | `BOT_STOPPED` | Bot process stopped gracefully |
| Bot | `BOT_ERROR` | Unhandled error in bot loop |
| Metrics | `METRICS_UPDATED` | Win-rate / PnL stats recalculated |
| Portfolio | `PORTFOLIO_UPDATED` | AUM / cash / invested changed |
| Alert | `ALERT_GENERATED` | Notification queued for dispatch |

---

## Architecture Diagram

```
                    ┌─────────────────────────────────┐
                    │           EVENT BUS              │
                    │  publish() / subscribe()         │
                    │  unsubscribe()                   │
                    └────────────┬────────────────────┘
                                 │
          ┌──────────────────────┼──────────────────────┐
          │                      │                      │
   ┌──────▼──────┐        ┌──────▼──────┐       ┌──────▼──────┐
   │   Scanner   │        │    Risk     │       │  Portfolio  │
   │   Service   │        │   Service   │       │   Service   │
   │             │        │             │       │             │
   │ PUBLISHES:  │        │ SUBSCRIBES: │       │ SUBSCRIBES: │
   │ SIGNAL_*    │        │ SIGNAL_*    │       │ POSITION_*  │
   └─────────────┘        │ PUBLISHES:  │       │ PUBLISHES:  │
                          │ CIRCUIT_*   │       │ PORTFOLIO_* │
                          └─────────────┘       └─────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │   Notification Service  │
                    │   SUBSCRIBES: ALERT_*   │
                    │   Dispatches: Telegram  │
                    └─────────────────────────┘
```

---

## Migration Roadmap

| Phase | Milestone | Status |
|---|---|---|
| V2.0 | Foundation scaffold + event bus skeleton | ✅ Done |
| V2.1 | Implement scanner_service (wraps V1 scanner) | Planned |
| V2.2 | Implement risk_service + circuit breaker events | Planned |
| V2.3 | Implement portfolio_service + storage adapters | Planned |
| V2.4 | Implement notification_service (replaces V1 Telegram) | Planned |
| V2.5 | Dashboard service reading from event bus | Planned |
| V2.6 | Full integration tests; parallel V1 shadow run | Planned |
| V2.7 | V1 retirement; V2 becomes production | Planned |

---

## Constraints

- **No V1 imports** inside `v2/`. V2 services will call V1 APIs over HTTP during the transition period (V2.1–V2.5), then cut over.
- **No migrations** in V2.0. Storage schema changes happen in V2.3+.
- **No code movement** from V1. V2 reimplements from scratch.
