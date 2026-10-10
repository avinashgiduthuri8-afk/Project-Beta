"""
V2 Subscriber Registry.

Central place where all service handlers are wired to the event bus.
Called once at application startup via register_all().

V2.1 wires: ScannerService (no inbound subscriptions needed for V2.1).
Later phases add: RiskService, PortfolioService, NotificationService.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .event_bus import EventBus
from .event_types import EventType

if TYPE_CHECKING:
    from background.ai.service import AIIntelligenceService
    from background.portfolio.service import PortfolioService
    from execution.risk.service import RiskService
    from execution.service import TradingService
    from execution.shadow.service import ShadowService
    from scanner.service import ScannerService
    from telegram.service import NotificationService

logger = logging.getLogger("core.bus.subscribers")


def register_all(
    bus: EventBus,
    scanner_service: ScannerService | None = None,
    ai_service: AIIntelligenceService | None = None,
    risk_service: RiskService | None = None,
    portfolio_service: PortfolioService | None = None,
    trading_service: TradingService | None = None,
    shadow_service: ShadowService | None = None,
    notification_service: NotificationService | None = None,
) -> None:
    """
    Wire all service handlers to the event bus.

    Parameters are optional so callers can register only the services
    available in the current phase.
    """
    if ai_service is not None:
        bus.subscribe(EventType.SIGNAL_GENERATED, ai_service.on_signal_generated)

    if risk_service is not None:
        bus.subscribe(
            EventType.SIGNAL_AI_CONFIRMED, risk_service.on_signal_ai_confirmed
        )
        bus.subscribe(EventType.POSITION_CLOSED, risk_service.on_position_closed)

    if portfolio_service is not None:
        bus.subscribe(EventType.POSITION_OPENED, portfolio_service._on_position_event)
        bus.subscribe(EventType.POSITION_CLOSED, portfolio_service._on_position_event)
        bus.subscribe(EventType.POSITION_UPDATED, portfolio_service._on_position_event)

    if trading_service is not None:
        bus.subscribe(EventType.TRADE_APPROVED, trading_service.on_trade_approved)

    if shadow_service is not None:
        bus.subscribe(
            EventType.SIGNAL_AI_REJECTED, shadow_service._on_signal_ai_rejected
        )
        bus.subscribe(EventType.TRADE_DENIED, shadow_service._on_trade_denied)

    logger.info(
        "V2 subscriber registry initialised — "
        "scanner=%s ai=%s risk=%s portfolio=%s trading=%s shadow=%s notif=%s",
        "✓" if scanner_service else "-",
        "✓" if ai_service else "-",
        "✓" if risk_service else "-",
        "✓" if portfolio_service else "-",
        "✓" if trading_service else "-",
        "✓" if shadow_service else "-",
        "✓" if notification_service else "-",
    )
