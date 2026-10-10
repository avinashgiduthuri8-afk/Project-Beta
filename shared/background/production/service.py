"""
V2 Production Service Facade.

Coordinates ProductionController, ProductionWatchdog, ShadowDivergenceTracker,
and ProductionRepository into a unified production management layer.
"""

from __future__ import annotations

from typing import Any

from core.bus.event_bus import EventBus
from core.logging import get_logger
from core.repository.production_repo import ProductionRepository
from execution.shadow.tracker import ShadowDivergenceTracker

from .controller import ProductionController
from .watchdog import ProductionWatchdog

logger = get_logger("background.production")


class ProductionService:
    """Production Deployment & Watchdog Supervisor Service."""

    def __init__(
        self,
        production_repo: ProductionRepository,
        bus: EventBus | None = None,
        services: dict[str, Any] | None = None,
        watchdog: ProductionWatchdog | None = None,
    ) -> None:
        self.repo = production_repo
        self._bus = bus
        self.controller = ProductionController(production_repo=production_repo, bus=bus)
        self.tracker = ShadowDivergenceTracker(production_repo=production_repo, bus=bus)
        self.watchdog = (
            watchdog
            if watchdog is not None
            else ProductionWatchdog(services=services, bus=bus)
        )
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        await self.controller.initialize_state()
        if not self.watchdog._running:
            await self.watchdog.start()
        logger.info(
            "ProductionService started with mode: %s", self.controller.mode.value
        )

    async def stop(self) -> None:
        self._started = False
        if self.watchdog._running:
            await self.watchdog.stop()
        logger.info("ProductionService stopped")

    async def get_status(self) -> dict[str, Any]:
        """Return unified production status snapshot."""
        health = await self.watchdog.inspect_system_health()
        return {
            "deployment_mode": self.controller.mode.value,
            "is_kill_switch_tripped": self.controller.is_kill_switch_tripped,
            "system_health": health,
            "wallet_limits_inr": {},
            "micro_order_caps_inr": {},
            "minimum_notional_inr": 200.0,
        }
