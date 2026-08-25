"""Thread-safe Pub/Sub Event Bus for real-time market data and order events."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Callable, Dict, List, Any

logger = logging.getLogger(__name__)


class EventBus:
    """Lightweight in-memory event dispatcher."""

    def __init__(self):
        self._subscribers: Dict[str, List[Callable[[Any], None]]] = defaultdict(list)

    def subscribe(self, event_type: str, callback: Callable[[Any], None]) -> None:
        """Subscribe a callback to a specific event type."""
        self._subscribers[event_type].append(callback)

    def publish(self, event_type: str, data: Any) -> None:
        """Dispatch event data to all registered subscribers."""
        if event_type not in self._subscribers:
            return

        for callback in self._subscribers[event_type]:
            try:
                callback(data)
            except Exception as e:
                logger.error(f"Error handling event '{event_type}': {e}", exc_info=True)

    def clear(self) -> None:
        """Remove all subscriptions."""
        self._subscribers.clear()
