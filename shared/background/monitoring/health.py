"""
V2 HealthChecker — aggregate liveness, readiness, and subsystem diagnostic probes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class HealthChecker:
    """Probes status across all active V2 subsystems and evaluates platform health."""

    def __init__(
        self,
        db: Any | None = None,
        scanner_service: Any | None = None,
        ai_service: Any | None = None,
        risk_service: Any | None = None,
        portfolio_service: Any | None = None,
        trading_service: Any | None = None,
        shadow_service: Any | None = None,
        notification_service: Any | None = None,
        scheduler: Any | None = None,
    ) -> None:
        self._db = db
        self._scanner_service = scanner_service
        self._ai_service = ai_service
        self._risk_service = risk_service
        self._portfolio_service = portfolio_service
        self._trading_service = trading_service
        self._shadow_service = shadow_service
        self._notification_service = notification_service
        self._scheduler = scheduler

    def check_health(self) -> dict[str, Any]:
        """Query all registered subsystems and determine aggregate health status."""
        services: dict[str, dict[str, Any]] = {}

        # 1. Database
        db_healthy = self._db is not None and self._db.is_open
        services["database"] = {"healthy": db_healthy}

        # Helper to probe health safely
        def probe_service(svc: Any) -> dict[str, Any]:
            if svc is None:
                return {"healthy": True, "registered": False}
            if hasattr(svc, "get_health"):
                return svc.get_health()
            if hasattr(svc, "running"):
                return {"healthy": bool(svc.running), "registered": True}
            return {"healthy": True, "registered": True}

        # 2. Scanner
        services["scanner"] = probe_service(self._scanner_service)

        # 3. AI Intelligence
        services["ai"] = probe_service(self._ai_service)

        # 4. Risk Engine
        services["risk"] = probe_service(self._risk_service)

        # 5. Portfolio
        services["portfolio"] = probe_service(self._portfolio_service)

        # 6. Trading
        services["trading"] = probe_service(self._trading_service)

        # 7. Shadow Engine
        services["shadow"] = probe_service(self._shadow_service)

        # 8. Notification
        services["notification"] = probe_service(self._notification_service)

        # 9. Scheduler
        services["scheduler"] = probe_service(self._scheduler)

        unhealthy = [
            k
            for k, v in services.items()
            if v.get("registered", True) is not False and not v.get("healthy", False)
        ]

        if not unhealthy:
            overall_status = "healthy"
        elif len(unhealthy) <= 2 and "database" not in unhealthy:
            overall_status = "degraded"
        else:
            overall_status = "unhealthy"

        return {
            "status": overall_status,
            "unhealthy_services": unhealthy,
            "services": services,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
