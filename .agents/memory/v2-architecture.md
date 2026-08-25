---
name: V2 Architecture decisions
description: Key decisions made during V2 Phase 1 architecture design — topology, constraints, and schema choices that implementation must honour.
---

## V2 runs on a separate port (5001), not mounted in app.py
V2 serves from `v2/app_v2.py` on port 5001. V1 `app.py` on port 5000 is never modified.
At V2.7 cut-over, `app.py` is replaced (delete + rename), not edited.

**Why:** "Do NOT modify any V1 files" is a hard user constraint. Mounting a router in app.py would violate it.

## Event routing rule: alerts always go through AlertManager → ALERT_GENERATED → NotificationService
Risk events (TRADE_DENIED, CAPITAL_LIMIT_HIT, CIRCUIT_BREAKER_TRIGGERED) are NOT subscribed to directly by NotificationService. They go: risk event → AlertManager → ALERT_GENERATED → NotificationService.

**Why:** Keeps NotificationService with a single subscription point; prevents duplicate alert dispatch paths.

## TradingService subscribes to TRADE_APPROVED only (not SIGNAL_GENERATED)
Signal evaluation is RiskService's job. TradingService is an executor, not a decision-maker.

**Why:** Prevents a second, unguarded path from signal → execution that bypasses capital limits.

## daily_pnl in RiskService maintained from POSITION_CLOSED (not TRADE_CLOSED)
TRADE_CLOSED is not a defined event. POSITION_CLOSED carries the pnl field. RiskService accumulates daily_pnl from POSITION_CLOSED events for drawdown/circuit-breaker logic.

**Why:** POSITION_CLOSED is the canonical "trade is done" event in the bus topology.

## positions table has exit_price, exit_reason, closed_at columns (NULLable until closed)
PositionRepository.close(id, exit_price, exit_reason) writes all three atomically. TradeRepository.insert() is called immediately after.

**Why:** Code review identified that PositionRepository.close() had no corresponding schema columns.

## V2.1 implementation complete — V2 runs on port 5001 alongside V1 on 5000
`v2/app_v2.py` is the entry point. Port 5001 is declared in .replit `[[ports]]` (externalPort=3000).
DB lives at `v2/data/alpha_v2.db`. Migration `001_core_tables.sql` is the only applied migration.
The `V2 application` workflow runs `python v2/app_v2.py` and reaches RUNNING state.

## Scheduler: per-job in-flight guard prevents concurrent task spawn
`BackgroundScheduler._inflight: dict[str, asyncio.Task]` — _tick() skips re-scheduling a job if its previous task is still running. Tasks are also drained on stop() before the DB closes.

**Why:** Code review caught that `last_run_at` alone (even set before fn()) doesn't protect against jobs whose execution exceeds their interval, which caused duplicate scanner writes/events.

## Signal adapter: use _parse_bool() not bool() for string fields (mtf_alignment)
`bool("none") == True` — always use `_parse_bool(raw)` from adapter.py for any boolean field from V1 API. Truthy strings: "true", "yes", "1" only.

## V1 scanner signals are all stale at current time (sideways market)
V1 `live_signals.json` has signals timestamped 2026-06-23 (2 weeks old). V2 correctly fetches 91 signals, generates+immediately expires them all (TTL=5min). live_signals=0 is correct. When V1 emits fresh signals, they will flow through normally.

## V2 SQLite uses WAL journal mode
Allows concurrent reads while one writer is active. Dashboard reads never block bot writes.

## All V2 architecture is in v2/ARCHITECTURE.md (1294 lines, Phase 1)
Covers: folder structure, shared core, event bus (28 events), 6 services, 7 repositories, SQLite schema, config system, scheduler (8 jobs), WebSocket feed, monitoring/metrics, 3 data flows, 8-phase migration roadmap.
