"""
Event Bus for Async / Multithreaded Event Dispatching.
Enables pub/sub routing between WebSocket feeds, strategies, and the OMS.
"""

from __future__ import annotations

import logging
import queue
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)


class EventBus:
    """Thread-safe event dispatcher for real-time market data and order events."""

    def __init__(self) -> None:
        self._listeners: Dict[str, List[Callable[[Any], None]]] = {}
        self._queue: queue.Queue = queue.Queue()

    def subscribe(self, event_type: str, callback: Callable[[Any], None]) -> None:
        """Subscribe a callback to a specific event type (e.g. 'tick', 'candle', 'order')."""
        if event_type not in self._listeners:
            self._listeners[event_type] = []
        self._listeners[event_type].append(callback)
        logger.debug(f"Subscribed callback {callback.__name__} to event: {event_type}")

    def publish(self, event_type: str, data: Any) -> None:
        """Publish event directly to all registered listeners."""
        listeners = self._listeners.get(event_type, [])
        for listener in listeners:
            try:
                listener(data)
            except Exception as e:
                logger.error(f"Error in event listener for {event_type}: {e}")
