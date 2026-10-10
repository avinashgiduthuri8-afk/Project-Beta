"""
Unit and Integration Tests for V2 PortfolioService (Task 2.1).

Verifies:
- Clean initialization
- Lifecycle startup and shutdown
- Event Bus connectivity and subscription registration
- Lifecycle idempotency
- Subscriptions cleanup on stop
"""

from __future__ import annotations

import pytest

from background.portfolio import PortfolioService
from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.bus.subscribers import register_all
from core.config import AppConfig


@pytest.mark.anyio
async def test_portfolio_service_initialization():
    """Verify PortfolioService initializes cleanly with required and optional dependencies."""
    bus = EventBus()
    cfg = AppConfig()

    service = PortfolioService(
        bus=bus,
        config=cfg,
    )

    assert service.bus is bus
    assert service.is_started is False
    health = service.get_health()
    assert health["healthy"] is False
    assert health["last_aum"] == 0.0
    assert health["last_deployed"] == 0.0


@pytest.mark.anyio
async def test_portfolio_service_startup():
    """Verify PortfolioService startup registers subscriptions and emits SYSTEM_STARTUP."""
    bus = EventBus()
    service = PortfolioService(bus=bus)

    # Track published events
    startup_events = []

    async def on_startup(event_type: EventType, payload: dict):
        startup_events.append((event_type, payload))

    bus.subscribe(EventType.SYSTEM_STARTUP, on_startup)

    await service.start()

    assert service.is_started is True
    assert service.get_health()["healthy"] is True

    # Verify event subscriptions exist on the bus
    assert bus.subscriber_count(EventType.POSITION_OPENED) >= 1
    assert bus.subscriber_count(EventType.POSITION_CLOSED) >= 1
    assert bus.subscriber_count(EventType.POSITION_UPDATED) >= 1

    # Verify SYSTEM_STARTUP was published with correct service name
    assert len(startup_events) == 1
    assert startup_events[0][0] == EventType.SYSTEM_STARTUP
    assert startup_events[0][1]["service"] == "portfolio_service"


@pytest.mark.anyio
async def test_portfolio_service_shutdown():
    """Verify PortfolioService shutdown unsubscribes from events and updates health."""
    bus = EventBus()
    service = PortfolioService(bus=bus)

    await service.start()
    assert service.is_started is True
    assert bus.subscriber_count(EventType.POSITION_OPENED) >= 1

    await service.stop()
    assert service.is_started is False
    assert service.get_health()["healthy"] is False

    # Verify subscriptions removed
    assert bus.subscriber_count(EventType.POSITION_OPENED) == 0
    assert bus.subscriber_count(EventType.POSITION_CLOSED) == 0
    assert bus.subscriber_count(EventType.POSITION_UPDATED) == 0


@pytest.mark.anyio
async def test_portfolio_service_lifecycle_idempotency():
    """Verify multiple start/stop calls are safe and idempotent."""
    bus = EventBus()
    service = PortfolioService(bus=bus)

    # Multiple starts
    await service.start()
    initial_listeners = bus.subscriber_count(EventType.POSITION_OPENED)
    await service.start()
    assert bus.subscriber_count(EventType.POSITION_OPENED) == initial_listeners

    # Multiple stops
    await service.stop()
    assert service.is_started is False
    await service.stop()
    assert service.is_started is False


@pytest.mark.anyio
async def test_portfolio_service_event_handling():
    """Verify that position mutation events dispatched on EventBus are safely processed."""
    bus = EventBus()
    service = PortfolioService(bus=bus)

    await service.start()

    # Dispatch events to ensure handlers execute cleanly without error
    await bus.publish(
        EventType.POSITION_OPENED,
        {"position_id": "pos_1", "coin": "SOL", "amount": 200.0},
    )
    await bus.publish(
        EventType.POSITION_UPDATED, {"position_id": "pos_1", "current_price": 105.0}
    )
    await bus.publish(EventType.POSITION_CLOSED, {"position_id": "pos_1", "pnl": 10.0})

    await service.stop()


@pytest.mark.anyio
async def test_portfolio_service_subscriber_registry():
    """Verify subscriber registry wires portfolio_service cleanly."""
    bus = EventBus()
    service = PortfolioService(bus=bus)

    register_all(bus=bus, portfolio_service=service)

    assert bus.subscriber_count(EventType.POSITION_OPENED) >= 1
    assert bus.subscriber_count(EventType.POSITION_CLOSED) >= 1
    assert bus.subscriber_count(EventType.POSITION_UPDATED) >= 1
