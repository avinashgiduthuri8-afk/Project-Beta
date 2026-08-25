"""
V2 Event Bus — public API.

Usage:
    from v2.bus import bus, EventType

    bus.subscribe(EventType.SIGNAL_GENERATED, my_handler)
    await bus.publish(EventType.SIGNAL_GENERATED, payload={"coin": "BTC"})
"""

from .event_bus import EventBus, bus
from .event_types import EventType

__all__ = ["EventBus", "bus", "EventType"]
