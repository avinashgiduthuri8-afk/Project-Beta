"""
PROJECT-ALPHA ShadowService — manages shadow simulation, scheduled price updates, and divergence tracking.
"""

from __future__ import annotations

from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.config import AppConfig
from core.logging import get_logger
from core.repository.event_log_repo import EventLogRepository
from core.repository.shadow_repo import ShadowRepository
from core.types import BotName

from .divergence import DivergenceTracker
from .engine import ShadowEngine

logger = get_logger("execution.shadow")


class ShadowService:
    """Coordinates shadow simulation and AI decision divergence tracking."""

    def __init__(
        self,
        bus: EventBus,
        shadow_repo: ShadowRepository,
        event_log_repo: EventLogRepository,
        config: AppConfig,
    ) -> None:
        self._bus = bus
        self._shadow_repo = shadow_repo
        self._event_log = event_log_repo
        self._config = config

        self._engine = ShadowEngine(
            bus=bus,
            shadow_repo=shadow_repo,
            event_log_repo=event_log_repo,
            config=config,
        )
        self._divergence_tracker = DivergenceTracker(
            bus=bus,
            shadow_repo=shadow_repo,
            event_log_repo=event_log_repo,
        )
        self._started = False

    @property
    def engine(self) -> ShadowEngine:
        return self._engine

    @property
    def divergence_tracker(self) -> DivergenceTracker:
        return self._divergence_tracker

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._bus.subscribe(EventType.SIGNAL_AI_REJECTED, self._on_signal_ai_rejected)
        self._bus.subscribe(EventType.TRADE_DENIED, self._on_trade_denied)
        await self._bus.publish(EventType.SYSTEM_STARTUP, {"service": "shadow_service"})
        logger.info("ShadowService started")

    async def stop(self) -> None:
        self._started = False
        self._bus.unsubscribe(EventType.SIGNAL_AI_REJECTED, self._on_signal_ai_rejected)
        self._bus.unsubscribe(EventType.TRADE_DENIED, self._on_trade_denied)
        logger.info("ShadowService stopped")

    # ── Event Handlers ────────────────────────────────────────────────────────

    async def _on_signal_ai_rejected(
        self, event_type: EventType, payload: dict
    ) -> None:
        """Record divergence when AI gates a signal."""
        try:
            signal_id = payload.get("signal_id") or "UNKNOWN"
            coin = payload.get("coin", "UNKNOWN")
            conflicts = payload.get("conflicts") or []
            reason = f"AI rejected signal (confidence {payload.get('confidence_score', 0)}): {', '.join(conflicts[:2])}"

            bot_str = payload.get("bot", "STE")
            try:
                bot = BotName(bot_str)
            except ValueError:
                bot = BotName.STE

            await self._divergence_tracker.record_divergence(
                signal_id=signal_id,
                bot=bot,
                coin=coin,
                v1_action="POTENTIAL_ENTRY",
                v2_action="AI_REJECTED",
                divergence_type="AI_FILTERED",
                reason=reason,
            )
        except Exception as exc:
            logger.warning(
                "Error processing SIGNAL_AI_REJECTED divergence",
                extra={"error": str(exc)},
            )

    async def _on_trade_denied(self, event_type: EventType, payload: dict) -> None:
        """Record divergence when Risk Engine blocks a trade."""
        try:
            signal_id = payload.get("signal_id") or "UNKNOWN"
            coin = payload.get("coin", "UNKNOWN")
            bot_str = payload.get("bot", "STE")
            try:
                bot = BotName(bot_str)
            except ValueError:
                bot = BotName.STE

            reason = (
                f"Risk engine blocked trade: {payload.get('reason', 'Capital limit')}"
            )
            await self._divergence_tracker.record_divergence(
                signal_id=signal_id,
                bot=bot,
                coin=coin,
                v1_action="POTENTIAL_ENTRY",
                v2_action="RISK_BLOCKED",
                divergence_type="RISK_FILTERED",
                reason=reason,
            )
        except Exception as exc:
            logger.warning(
                "Error processing TRADE_DENIED divergence", extra={"error": str(exc)}
            )

    # ── Queries & State ───────────────────────────────────────────────────────

    async def get_summary(self) -> dict:
        return await self._shadow_repo.get_divergence_summary()

    def get_health(self) -> dict:
        return {
            "healthy": self._started,
            "shadow_mode": self._config.shadow_mode,
        }
