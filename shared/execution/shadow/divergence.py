"""
V2 DivergenceTracker — cross-references V2 decisions with V1 bot activities.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.logging import get_logger
from core.repository.event_log_repo import EventLogRepository
from core.repository.shadow_repo import ShadowRepository
from core.types import BotName, DecisionDivergence

logger = get_logger("execution.shadow.divergence")


class DivergenceTracker:
    """Tracks discrepancies between legacy V1 executions and V2 AI/Risk decisions."""

    def __init__(
        self,
        bus: EventBus,
        shadow_repo: ShadowRepository,
        event_log_repo: EventLogRepository,
    ) -> None:
        self._bus = bus
        self._shadow_repo = shadow_repo
        self._event_log = event_log_repo

    async def record_divergence(
        self,
        signal_id: str,
        bot: BotName,
        coin: str,
        v1_action: str,
        v2_action: str,
        divergence_type: str,
        reason: str,
        v1_pnl: float | None = None,
        v2_simulated_pnl: float | None = None,
    ) -> DecisionDivergence:
        now = datetime.now(timezone.utc)
        divergence = DecisionDivergence(
            id=str(uuid.uuid4()),
            signal_id=signal_id,
            bot=bot,
            coin=coin.upper(),
            v1_action=v1_action,
            v2_action=v2_action,
            divergence_type=divergence_type,
            reason=reason,
            detected_at=now,
            v1_pnl=v1_pnl,
            v2_simulated_pnl=v2_simulated_pnl,
        )

        await self._shadow_repo.insert_divergence(divergence)

        div_payload = {
            "divergence_id": divergence.id,
            "signal_id": signal_id,
            "bot": bot.value,
            "coin": coin,
            "v1_action": v1_action,
            "v2_action": v2_action,
            "divergence_type": divergence_type,
            "reason": reason,
            "detected_at": now.isoformat(),
        }

        await self._bus.publish(EventType.DIVERGENCE_DETECTED, div_payload)
        await self._event_log.append(
            event_type=EventType.DIVERGENCE_DETECTED.value,
            source_service="divergence_tracker",
            entity_id=divergence.id,
            payload=div_payload,
        )
        logger.info(
            "Decision divergence RECORDED",
            extra={"coin": coin, "type": divergence_type},
        )
        return divergence
