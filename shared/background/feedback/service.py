"""
V2 Feedback Service.

Unified service facade coordinating FeedbackOrchestrator and FeedbackRepository.
Exposes loop status, audit trail queries, and trigger-cycle execution for REST API endpoints.
"""

from __future__ import annotations

from typing import Any

from background.backtest.service.service import BacktestService
from core.bus.event_bus import EventBus
from core.logging import get_logger
from core.repository.feedback_repo import FeedbackRepository

from .orchestrator import FeedbackOrchestrator

logger = get_logger("background.feedback")


class FeedbackService:
    """Autonomous Recursive Feedback Loop Service Facade."""

    def __init__(
        self,
        feedback_repo: FeedbackRepository,
        backtest_service: BacktestService,
        bus: EventBus | None = None,
        orchestrator: FeedbackOrchestrator | None = None,
    ) -> None:
        self._feedback_repo = feedback_repo
        self._backtest_service = backtest_service
        self._bus = bus
        self.orchestrator = orchestrator or FeedbackOrchestrator(
            feedback_repo=self._feedback_repo,
            backtest_service=self._backtest_service,
            bus=self._bus,
        )
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        logger.info(
            "FeedbackService started with FeedbackOrchestrator & Pre-Deployment Gate"
        )

    async def stop(self) -> None:
        self._started = False
        logger.info("FeedbackService stopped")

    async def get_loop_status(self) -> dict[str, Any]:
        """Return current autonomous feedback loop state and active calibrations cache."""
        calibrations = await self._feedback_repo.get_all_active_calibrations()
        history = await self._feedback_repo.get_audit_history(limit=10)

        return {
            "loop_status": "ACTIVE_HEALTHY",
            "active_calibrations": calibrations,
            "recent_audit_events_count": len(history),
            "recent_audit_events": history,
        }

    async def get_audit_trail(
        self, bot_name: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Fetch chronological feedback audit trail events."""
        return await self._feedback_repo.get_audit_history(
            bot_name=bot_name, limit=limit
        )

    async def trigger_feedback_cycle(
        self,
        bot_name: str = "STE",
        pair: str = "BTC/INR",
        multiplier: float = 1.0,
        threshold: float = 85.0,
        candles: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Trigger an immediate autonomous feedback evaluation & backtest validation pass."""
        return await self.orchestrator.evaluate_and_validate_calibration(
            bot_name=bot_name,
            pair=pair,
            proposed_multiplier=multiplier,
            proposed_threshold=threshold,
            validation_candles=candles,
        )
