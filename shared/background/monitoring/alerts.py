"""
V2 AlertManager — monitors health checks and metrics thresholds, auto-emitting alert events.
"""

from __future__ import annotations

from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.logging import get_logger

from .health import HealthChecker
from .metrics import MetricsCollector

logger = get_logger("background.monitoring.alerts")


class AlertManager:
    """Watches subsystem metrics and health probes to trigger automated alerts."""

    def __init__(
        self,
        bus: EventBus,
        health_checker: HealthChecker,
        metrics: MetricsCollector | None = None,
    ) -> None:
        self._bus = bus
        self._health_checker = health_checker
        self._metrics = metrics or MetricsCollector()
        self._last_status = "healthy"

    async def evaluate_thresholds(self) -> dict:
        """Run health check and publish alert events if degradation is detected."""
        health = self._health_checker.check_health()
        current_status = health["status"]

        if current_status != "healthy" and self._last_status == "healthy":
            # Subsystem degraded
            unhealthy_list = ", ".join(health["unhealthy_services"])
            alert_payload = {
                "level": "WARNING" if current_status == "degraded" else "ERROR",
                "title": f"System Status: {current_status.upper()}",
                "message": f"Subsystem issue detected in: {unhealthy_list}",
            }
            await self._bus.publish(EventType.HEALTH_DEGRADED, alert_payload)
            await self._bus.publish(EventType.ALERT_GENERATED, alert_payload)
            logger.warning(
                "System health DEGRADED",
                extra={"unhealthy": health["unhealthy_services"]},
            )

        elif current_status == "healthy" and self._last_status != "healthy":
            # Subsystem recovered
            alert_payload = {
                "level": "INFO",
                "title": "System Health Recovered",
                "message": "All subsystems are operating normally.",
            }
            await self._bus.publish(EventType.HEALTH_RECOVERED, alert_payload)
            await self._bus.publish(EventType.ALERT_GENERATED, alert_payload)
            logger.info("System health RECOVERED")

        self._last_status = current_status
        return health
