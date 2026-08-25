# V2 FOUNDATION — Implementation Report

**Date**: 2026-07-04  
**Task**: Create V2 architecture scaffold with event bus skeleton  
**Status**: ✅ Complete — scaffolding only, zero V1 coupling

---

## What Was Created

```
v2/
├── __init__.py
├── bus/
│   ├── __init__.py
│   ├── event_bus.py         ← async publish/subscribe/unsubscribe
│   ├── event_types.py       ← EventType enum (16 event constants)
│   └── subscribers.py       ← handler registry skeleton
│
├── services/
│   ├── __init__.py
│   ├── scanner_service/     ← __init__.py (skeleton)
│   ├── risk_service/        ← __init__.py (skeleton)
│   ├── portfolio_service/   ← __init__.py (skeleton)
│   ├── dashboard_service/   ← __init__.py (skeleton)
│   └── notification_service/ ← __init__.py (skeleton)
│
├── storage/                 ← __init__.py (skeleton)
├── analytics/               ← __init__.py (skeleton)
├── tests/                   ← __init__.py (placeholder)
└── README.md                ← full architecture + migration roadmap
```

---

## Event Definitions

### Signal Events
| Event | Value | Trigger |
|---|---|---|
| `SIGNAL_GENERATED` | `signal.generated` | New signal from scanner |
| `SIGNAL_UPDATED` | `signal.updated` | Score/metadata changed |
| `SIGNAL_EXPIRED` | `signal.expired` | TTL elapsed or overridden |

### Position Events
| Event | Value | Trigger |
|---|---|---|
| `POSITION_OPENED` | `position.opened` | Trade entered |
| `POSITION_CLOSED` | `position.closed` | TP / SL / manual exit |
| `POSITION_UPDATED` | `position.updated` | Trailing stop / DCA update |

### Risk Events
| Event | Value | Trigger |
|---|---|---|
| `CAPITAL_LIMIT_HIT` | `risk.capital_limit_hit` | Deployed ≥ limit |
| `DRAWDOWN_LIMIT_HIT` | `risk.drawdown_limit_hit` | Daily drawdown ≥ threshold |
| `CIRCUIT_BREAKER_TRIGGERED` | `risk.circuit_breaker_triggered` | All bots halted |

### Bot Lifecycle Events
| Event | Value | Trigger |
|---|---|---|
| `BOT_STARTED` | `bot.started` | Bot loop initialized |
| `BOT_STOPPED` | `bot.stopped` | Graceful shutdown |
| `BOT_ERROR` | `bot.error` | Unhandled exception in loop |

### Metrics / Portfolio Events
| Event | Value | Trigger |
|---|---|---|
| `METRICS_UPDATED` | `metrics.updated` | Win-rate / PnL recalculated |
| `PORTFOLIO_UPDATED` | `portfolio.updated` | AUM / cash / invested changed |
| `ALERT_GENERATED` | `alert.generated` | Notification queued |

---

## Event Bus API

```python
from v2.bus import bus, EventType

# Subscribe a handler
bus.subscribe(EventType.SIGNAL_GENERATED, my_async_handler)

# Publish an event
await bus.publish(EventType.SIGNAL_GENERATED, payload={"coin": "BTC"})

# Unsubscribe
bus.unsubscribe(EventType.SIGNAL_GENERATED, my_async_handler)

# Inspect
bus.subscriber_count(EventType.SIGNAL_GENERATED)  # → int
bus.all_subscriptions()                             # → dict[str, list[str]]
```

Handler signature:
```python
async def my_async_handler(event_type: EventType, payload: dict) -> None: ...
```

---

## Architecture

```
                    ┌─────────────────────────────────┐
                    │           EVENT BUS              │
                    │  publish() subscribe()           │
                    │  unsubscribe()                   │
                    └────────────┬────────────────────┘
                                 │
          ┌──────────────────────┼──────────────────────┐
          │                      │                      │
   ┌──────▼──────┐        ┌──────▼──────┐       ┌──────▼──────┐
   │   Scanner   │        │    Risk     │       │  Portfolio  │
   │   Service   │        │   Service   │       │   Service   │
   └─────────────┘        └─────────────┘       └─────────────┘
          │                      │                      │
          └──────────────────────▼──────────────────────┘
                    ┌────────────────────────┐
                    │   Notification Service │
                    │   Dashboard Service    │
                    └────────────────────────┘
```

---

## Constraints Enforced

- ✅ No V1 imports anywhere inside `v2/`
- ✅ No migrations, no schema changes
- ✅ No code movement from V1
- ✅ V1 continues to run identically; V2 is purely additive
- ✅ Event bus skeleton compiles with no external dependencies

---

## Migration Roadmap

| Phase | Milestone |
|---|---|
| **V2.0** (now) | Foundation scaffold + event bus skeleton |
| **V2.1** | `scanner_service` wraps V1 scanner; publishes `SIGNAL_*` |
| **V2.2** | `risk_service` + circuit breaker events |
| **V2.3** | `portfolio_service` + async storage adapters |
| **V2.4** | `notification_service` replaces V1 Telegram bots |
| **V2.5** | `dashboard_service` reads from event bus; no more V1 globals |
| **V2.6** | Full integration tests; V1 and V2 run in parallel (shadow mode) |
| **V2.7** | V1 retirement; V2 becomes production |

---

## Regressions

None. V1 is untouched. The `v2/` directory is entirely new.

---

## Tests Run

- App restarted after scaffold creation: ✅
- V1 scanner, bots, and dashboard running normally: ✅
- `from v2.bus import bus, EventType` importable: ✅
