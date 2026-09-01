"""Thread-safe In-Memory Central Event Queue."""

from __future__ import annotations

import queue
import logging
from typing import Optional, Dict, Any, List, Callable
from sector_quant.events.events import Event, EventType

logger = logging.getLogger(__name__)


class EventQueue:
    """Central synchronized FIFO event queue coordinating data, strategy, portfolio, and execution."""

    def __init__(self, maxsize: int = 0):
        self._queue: queue.Queue[Event] = queue.Queue(maxsize=maxsize)
        self._history: List[Event] = []
        self._counts: Dict[EventType, int] = {e: 0 for e in EventType}
        self._subscribers: Dict[EventType, List[Callable[[Event], None]]] = {e: [] for e in EventType}
        self._record_history: bool = False

    def put(self, event: Event) -> None:
        """Pushes an event into the queue and notifies any listeners."""
        self._queue.put(event)
        self._counts[event.type] = self._counts.get(event.type, 0) + 1

        if self._record_history:
            self._history.append(event)

        for listener in self._subscribers.get(event.type, []):
            try:
                listener(event)
            except Exception as e:
                logger.error(f"Error executing event listener for {event.type}: {e}", exc_info=True)

    def get(self, block: bool = True, timeout: Optional[float] = None) -> Event:
        """Pops the next event in FIFO order."""
        return self._queue.get(block=block, timeout=timeout)

    def empty(self) -> bool:
        """Returns True if the queue has no pending events."""
        return self._queue.empty()

    def qsize(self) -> int:
        """Returns the current number of events in the queue."""
        return self._queue.qsize()

    def subscribe(self, event_type: EventType, callback: Callable[[Event], None]) -> None:
        """Attaches an observer callback to a specific event type."""
        self._subscribers[event_type].append(callback)

    def clear(self) -> None:
        """Clears all pending events from the queue."""
        with self._queue.mutex:
            self._queue.queue.clear()

    def enable_history(self, enable: bool = True) -> None:
        self._record_history = enable

    @property
    def event_counts(self) -> Dict[EventType, int]:
        return dict(self._counts)

    @property
    def history(self) -> List[Event]:
        return list(self._history)

