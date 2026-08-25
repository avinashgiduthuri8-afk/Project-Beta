"""
V2 Subscriber Registry.

Central place where all service handlers are wired to the event bus.
Called once at application startup via register_all().

V2.1 wires: ScannerService (no inbound subscriptions needed for V2.1).
Later phases add: RiskService, PortfolioService, NotificationService, DashboardService.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .event_bus import EventBus
from .event_types import EventType

if TYPE_CHECKING:
    from v2.services.scanner_service import ScannerService

logger = logging.getLogger("v2.bus.subscribers")


def register_all(
    bus: EventBus,
    scanner_service: "ScannerService | None" = None,
) -> None:
    """
    Wire all service handlers to the event bus.

    Parameters are optional so callers can register only the services
    available in the current phase.
    """
    # ── V2.1: No inbound subscriptions for ScannerService ────────────────────
    # ScannerService is a publisher only in V2.1. It publishes SIGNAL_GENERATED
    # and SIGNAL_EXPIRED events. Subscriptions will be added in V2.2+ as
    # RiskService, PortfolioService, and DashboardService come online.

    logger.info(
        "V2 subscriber registry initialised — "
        "scanner_service=%s",
        "connected" if scanner_service else "not provided",
    )


# ── Placeholder handlers (filled in as phases land) ──────────────────────────

async def on_signal_generated(event_type: EventType, payload: dict) -> None:
    """V2.2: RiskService evaluates signal for capital pre-check."""
    pass


async def on_signal_expired(event_type: EventType, payload: dict) -> None:
    """V2.2+: Cancel any pending TRADE_APPROVED for this signal."""
    pass


async def on_position_opened(event_type: EventType, payload: dict) -> None:
    """V2.3: PortfolioService updates deployed capital cache."""
    pass


async def on_position_closed(event_type: EventType, payload: dict) -> None:
    """V2.3: PortfolioService updates cash + PnL; RiskService updates daily_pnl."""
    pass


async def on_capital_limit_hit(event_type: EventType, payload: dict) -> None:
    """V2.2: AlertManager emits ALERT_GENERATED (WARN level)."""
    pass


async def on_circuit_breaker_triggered(event_type: EventType, payload: dict) -> None:
    """V2.2: TradingService halts; AlertManager emits ALERT_GENERATED (CRITICAL)."""
    pass


async def on_alert_generated(event_type: EventType, payload: dict) -> None:
    """V2.4: NotificationService dispatches to Telegram."""
    pass


async def on_job_failed(event_type: EventType, payload: dict) -> None:
    """V2.4: NotificationService dispatches job-failure alert."""
    pass
