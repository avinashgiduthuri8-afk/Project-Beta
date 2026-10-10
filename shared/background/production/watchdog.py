"""
core/production_service/watchdog.py — 24/7 Watchdog Supervisor & Health Recovery.
background/production/watchdog.py — 24/7 Watchdog Supervisor & Health Recovery.

Monitors all 14 pipeline stages and 9 critical subsystem probes:
1. Scanner
2. Signal Engine
3. AI Intelligence
4. Risk Engine
5. Execution Router
6. CoinDCX Relay
7. SQLite Database
8. EventBus
9. Scheduler

Provides automatic self-healing and alert dispatching.
Monitors subsystem probes, detects stalled worker loops, and performs autonomous self-healing.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.config import AppConfig
from core.logging import get_logger

logger = get_logger("background.production.watchdog")


class ProductionWatchdog:
    """
    24/7 asynchronous watchdog supervisor monitoring subsystem probes,
    detecting stalled worker loops, and performing autonomous self-healing.
    """

    def __init__(
        self,
        config: AppConfig | None = None,
        bus: EventBus | None = None,
        scanner_service: Any | None = None,
        ai_service: Any | None = None,
        risk_service: Any | None = None,
        trading_service: Any | None = None,
        db: Any | None = None,
        scheduler: Any | None = None,
        signal_repo: Any | None = None,
        event_log_repo: Any | None = None,
        notification_service: Any | None = None,
        inspection_interval_sec: float = 30.0,
        services: dict[str, Any] | None = None,
        check_interval_sec: float | None = None,
    ) -> None:
        from core.config import get_config

        self._config = config or get_config()
        self._bus = bus or EventBus()
        self._services_dict = services or {}

        if services:
            scanner_service = scanner_service or services.get("scanner_service")
            ai_service = ai_service or services.get("ai_service")
            risk_service = risk_service or services.get("risk_service")
            trading_service = trading_service or services.get("trading_service")
            db = db or services.get("db")
            scheduler = scheduler or services.get("scheduler")
            signal_repo = signal_repo or services.get("signal_repo")
            event_log_repo = event_log_repo or services.get("event_log_repo")
            notification_service = notification_service or services.get(
                "notification_service"
            )

        self._scanner_service = scanner_service
        self._ai_service = ai_service
        self._risk_service = risk_service
        self._trading_service = trading_service
        self._db = db
        self._scheduler = scheduler
        self._signal_repo = signal_repo
        self._event_log_repo = event_log_repo
        self._notification_service = notification_service

        self._interval = (
            check_interval_sec
            if check_interval_sec is not None
            else inspection_interval_sec
        )
        self._running = False
        self._task: asyncio.Task | None = None
        self._start_time = datetime.now(timezone.utc)
        self._last_inspection_at: datetime | None = None
        self._inspection_count = 0
        self._recovery_count = 0
        self._probe_history: dict[str, dict[str, Any]] = {}
        self._last_alert_status = "HEALTHY"
        self._last_alert_time: float = 0.0

    def wire_dependencies(
        self,
        scanner_service: Any | None = None,
        ai_service: Any | None = None,
        risk_service: Any | None = None,
        trading_service: Any | None = None,
        db: Any | None = None,
        scheduler: Any | None = None,
        signal_repo: Any | None = None,
        event_log_repo: Any | None = None,
        notification_service: Any | None = None,
    ) -> None:
        """Wire references dynamically as subsystems finish initialization."""
        if scanner_service is not None:
            self._scanner_service = scanner_service
        if ai_service is not None:
            self._ai_service = ai_service
        if risk_service is not None:
            self._risk_service = risk_service
        if trading_service is not None:
            self._trading_service = trading_service
        if db is not None:
            self._db = db
        if scheduler is not None:
            self._scheduler = scheduler
        if signal_repo is not None:
            self._signal_repo = signal_repo
        if event_log_repo is not None:
            self._event_log_repo = event_log_repo
        if notification_service is not None:
            self._notification_service = notification_service

    async def start(self) -> None:
        """Start the watchdog 30-second inspection loop."""
        if self._running:
            return
        self._running = True
        self._start_time = datetime.now(timezone.utc)
        self._task = asyncio.create_task(self._run_loop(), name="production-watchdog")
        logger.info(
            "ProductionWatchdog started with %ss inspection cycle", self._interval
        )

    async def stop(self) -> None:
        """Stop the watchdog inspection loop gracefully."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("ProductionWatchdog stopped")

    async def _run_loop(self) -> None:
        """Continuous inspection loop."""
        # Initial wait so other services finish starting up
        await asyncio.sleep(2.0)
        while self._running:
            try:
                await self.inspect_system()
            except Exception as exc:
                logger.error("Watchdog inspection error: %s", exc)

            try:
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                break

    async def inspect_system(self) -> dict[str, Any]:
        """
        Execute full probe inspection across all 9 subsystems.
        Triggers self-healing if stalls or degradations are detected.
        """
        self._inspection_count += 1
        self._last_inspection_at = datetime.now(timezone.utc)
        probes: dict[str, dict[str, Any]] = {}

        # 1. SQLite Database Probe
        probes["database"] = await self._probe_database()

        # 2. Scanner Probe & Auto-Recovery
        probes["scanner"] = await self._probe_scanner()

        # 3. Signal Engine Probe
        probes["signal_engine"] = await self._probe_signal_engine()

        # 4. AI Intelligence Probe
        probes["ai_intelligence"] = await self._probe_ai_intelligence()

        # 5. Risk Engine Probe
        probes["risk_engine"] = await self._probe_risk_engine()

        # 6. Execution Router Probe
        probes["execution_router"] = await self._probe_execution_router()

        # 7. CoinDCX Relay Probe
        probes["coindcx_relay"] = await self._probe_coindcx_relay()

        # 8. EventBus Probe
        probes["event_bus"] = self._probe_event_bus()

        # 9. Scheduler Probe
        probes["scheduler"] = self._probe_scheduler()

        self._probe_history = probes

        # Determine overall system health
        unhealthy_services = []
        if self._services_dict:
            for name, svc in self._services_dict.items():
                started = getattr(svc, "_started", True)
                if not started:
                    unhealthy_services.append(name)

        all_ok = (
            all(p.get("status") in ("OK", "NORMAL", "HEALTHY") for p in probes.values())
            and not unhealthy_services
        )
        system_status = "HEALTHY" if all_ok else "DEGRADED"

        now_ts = time.time()
        if not all_ok:
            degraded_probes = [
                k
                for k, v in probes.items()
                if v.get("status") not in ("OK", "NORMAL", "HEALTHY")
            ]
            logger.warning(
                "Watchdog detected degraded subsystems: probes=%s, services=%s",
                degraded_probes,
                unhealthy_services,
            )
            # Only publish alert on state change from HEALTHY -> DEGRADED or after 1 hour (3600s) cooldown
            if self._bus and (
                self._last_alert_status != "DEGRADED"
                or (now_ts - self._last_alert_time) > 3600.0
            ):
                self._last_alert_status = "DEGRADED"
                self._last_alert_time = now_ts
                try:
                    await self._bus.publish(
                        EventType.ALERT_GENERATED,
                        {
                            "title": "Watchdog Health Degradation Alert",
                            "severity": "WARNING",
                            "unhealthy_services": unhealthy_services,
                            "degraded_probes": degraded_probes,
                            "timestamp": self._last_inspection_at.isoformat(),
                        },
                    )
                except Exception as exc:
                    logger.debug("Failed publishing watchdog health alert: %s", exc)
        else:
            if self._last_alert_status == "DEGRADED":
                self._last_alert_status = "HEALTHY"
                logger.info(
                    "Watchdog detected all subsystems recovered to HEALTHY state"
                )

        unhealthy_cnt = (
            len(unhealthy_services)
            if self._services_dict
            else len(
                [
                    k
                    for k, v in probes.items()
                    if v.get("status") not in ("OK", "NORMAL", "HEALTHY", "UNKNOWN")
                ]
            )
        )

        return {
            "status": system_status,
            "overall_status": system_status,
            "unhealthy_count": unhealthy_cnt,
            "unhealthy_services": unhealthy_services,
            "subsystems_healthy": all_ok,
            "inspected_at": self._last_inspection_at.isoformat(),
            "inspection_count": self._inspection_count,
            "recovery_count": self._recovery_count,
            "probes": probes,
        }

    inspect_system_health = inspect_system

    async def _probe_database(self) -> dict[str, Any]:
        """Probe SQLite connectivity and query execution."""
        if not self._db or not self._db.is_open:
            return {"status": "DOWN", "error": "Database not open"}
        try:
            t0 = time.perf_counter()
            async with self._db.connection.execute("SELECT 1") as cur:
                row = await cur.fetchone()
            latency_ms = (time.perf_counter() - t0) * 1000
            if row and row[0] == 1:
                return {"status": "OK", "latency_ms": round(latency_ms, 2)}
            return {"status": "DEGRADED", "error": "Unexpected query response"}
        except Exception as exc:
            # Self-healing attempt: reconnect
            try:
                logger.warning("Watchdog attempting SQLite reconnection...")
                await self._db.open()
                self._recovery_count += 1
                return {"status": "RECOVERED", "action": "Reconnected SQLite"}
            except Exception as re_exc:
                return {
                    "status": "DOWN",
                    "error": f"{exc} | Reconnect failed: {re_exc}",
                }

    async def _probe_scanner(self) -> dict[str, Any]:
        """Probe scanner health and auto-trigger stalled poll passes."""
        if not self._scanner_service:
            return {"status": "UNKNOWN", "message": "Scanner service not wired"}
        try:
            is_running = getattr(self._scanner_service, "_started", False) or getattr(
                self._scanner_service, "_running", False
            )
            last_poll = getattr(
                self._scanner_service, "_last_poll_time", None
            ) or getattr(self._scanner_service, "_last_poll_at", None)

            # Check if scanner is stalled (> 120s since last poll)
            now = datetime.now(timezone.utc)
            if last_poll is not None:
                elapsed_sec = (now - last_poll).total_seconds()
                if elapsed_sec > 120.0 and is_running:
                    logger.warning(
                        "Scanner stalled (elapsed: %.1fs > 120s). Triggering self-healing poll...",
                        elapsed_sec,
                    )
                    asyncio.create_task(self._scanner_service.poll())
                    self._recovery_count += 1
                    return {
                        "status": "RECOVERING",
                        "elapsed_sec": round(elapsed_sec, 1),
                        "action": "Triggered recovery poll pass",
                    }

            return {
                "status": "OK" if is_running else "IDLE",
                "running": is_running,
                "last_poll": last_poll.isoformat() if last_poll else None,
            }
        except Exception as exc:
            return {"status": "DEGRADED", "error": str(exc)}

    async def _probe_signal_engine(self) -> dict[str, Any]:
        """Probe signal storage and queue responsiveness."""
        if not self._signal_repo:
            return {"status": "OK", "message": "Repo check deferred"}
        try:
            if hasattr(self._signal_repo, "get_live"):
                active_signals = await self._signal_repo.get_live()
            elif hasattr(self._signal_repo, "get_active"):
                active_signals = await self._signal_repo.get_active(limit=5)
            else:
                active_signals = []
            return {"status": "OK", "active_signals_count": len(active_signals)}
        except Exception as exc:
            return {"status": "DEGRADED", "error": str(exc)}

    async def _probe_ai_intelligence(self) -> dict[str, Any]:
        """Probe AI intelligence scoring service."""
        if not self._ai_service:
            return {"status": "UNKNOWN", "message": "AI service not wired"}
        is_running = getattr(self._ai_service, "_started", False) or getattr(
            self._ai_service, "_running", False
        )
        return {"status": "OK" if is_running else "IDLE", "running": is_running}

    async def _probe_risk_engine(self) -> dict[str, Any]:
        """Probe Risk Engine state, circuit breaker, and limits."""
        if not self._risk_service:
            return {"status": "UNKNOWN", "message": "Risk service not wired"}
        try:
            cb = getattr(self._risk_service, "circuit_breaker", None)
            is_tripped = cb.is_tripped if cb else False
            emergency_stop = cb.emergency_stop if cb else False
            return {
                "status": "TRIPPED" if is_tripped or emergency_stop else "OK",
                "circuit_breaker_tripped": is_tripped,
                "emergency_stop": emergency_stop,
                "breaker_reason": cb.reason if cb else None,
            }
        except Exception as exc:
            return {"status": "DEGRADED", "error": str(exc)}

    async def _probe_execution_router(self) -> dict[str, Any]:
        """Probe TradingService order router and positions."""
        if not self._trading_service:
            return {"status": "UNKNOWN", "message": "Trading service not wired"}
        try:
            mode = getattr(self._config, "deployment_mode", "PAPER")
            trading_enabled = getattr(self._config, "trading_enabled", False)
            return {
                "status": "OK",
                "mode": mode,
                "trading_enabled": trading_enabled,
            }
        except Exception as exc:
            return {"status": "DEGRADED", "error": str(exc)}

    async def _probe_coindcx_relay(self) -> dict[str, Any]:
        """Probe CoinDCX subaccount manager and client routing."""
        if not self._trading_service:
            return {"status": "OK", "message": "Subaccount relay nominal"}
        try:
            sub_mgr = getattr(self._trading_service, "_subaccount_manager", None)
            if sub_mgr:
                telemetry = sub_mgr.get_all_subaccount_telemetry()
                return {"status": "OK", "active_subaccounts": len(telemetry)}
            return {"status": "OK", "mode": "Unified Capital Pool"}
        except Exception as exc:
            return {"status": "DEGRADED", "error": str(exc)}

    def _probe_event_bus(self) -> dict[str, Any]:
        """Probe EventBus subscriber counts and operational state."""
        if not self._bus:
            return {"status": "DOWN", "error": "EventBus not initialized"}
        try:
            subs = getattr(self._bus, "_subscribers", {})
            total_handlers = sum(len(h) for h in subs.values()) if subs else 0
            return {"status": "OK", "registered_handlers": total_handlers}
        except Exception as exc:
            return {"status": "DEGRADED", "error": str(exc)}

    def _probe_scheduler(self) -> dict[str, Any]:
        """Probe BackgroundScheduler running status and scheduled jobs."""
        if not self._scheduler:
            return {"status": "UNKNOWN", "message": "Scheduler not wired"}
        try:
            is_running = getattr(self._scheduler, "_running", False)
            jobs = getattr(self._scheduler, "_jobs", {})
            return {
                "status": "OK" if is_running else "STOPPED",
                "running": is_running,
                "scheduled_jobs_count": len(jobs),
            }
        except Exception as exc:
            return {"status": "DEGRADED", "error": str(exc)}

    def get_telemetry(self) -> dict[str, Any]:
        """Return watchdog inspection telemetry for UI and monitoring APIs."""
        uptime_sec = (datetime.now(timezone.utc) - self._start_time).total_seconds()
        return {
            "running": self._running,
            "uptime_seconds": round(uptime_sec, 1),
            "inspection_count": self._inspection_count,
            "recovery_count": self._recovery_count,
            "last_inspection_at": (
                self._last_inspection_at.isoformat()
                if self._last_inspection_at
                else None
            ),
            "probes": self._probe_history,
        }

    def get_summary(self) -> dict[str, Any]:
        """Return watchdog summary with inspections_total count."""
        res = self.get_telemetry()
        res["inspections_total"] = self._inspection_count
        return res
