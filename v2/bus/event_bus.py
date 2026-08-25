"""
V2 Event Bus — Skeleton only.

Provides publish / subscribe / unsubscribe primitives.
No production implementation yet — all methods are stubs that log intent.

Usage (future):
    from v2.bus.event_bus import EventBus
    from v2.bus.event_types import EventType

    bus = EventBus()
    bus.subscribe(EventType.SIGNAL_GENERATED, my_handler)
    await bus.publish(EventType.SIGNAL_GENERATED, payload={"coin": "BTC"})
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Awaitable, Callable

from .event_types import EventType

logger = logging.getLogger("v2.event_bus")

Handler = Callable[[EventType, dict], Awaitable[None]]


class EventBus:
    """
    Lightweight async pub/sub bus.

    Thread-safety: designed for use within a single asyncio event loop.
    For multi-process or cross-service messaging, swap this for a message
    broker (Redis Streams, RabbitMQ) in V2.1+.
    """

    def __init__(self) -> None:
        self._subscribers: dict[EventType, list[Handler]] = defaultdict(list)

    # ── Subscribe ───────────────────────────────────────────────────────────

    def subscribe(self, event_type: EventType, handler: Handler) -> None:
        """
        Register *handler* to be called whenever *event_type* is published.

        A handler is an async callable with signature:
            async def handler(event_type: EventType, payload: dict) -> None
        """
        if handler not in self._subscribers[event_type]:
            self._subscribers[event_type].append(handler)
            logger.debug("Subscribed %s → %s", event_type, handler.__name__)

    # ── Unsubscribe ─────────────────────────────────────────────────────────

    def unsubscribe(self, event_type: EventType, handler: Handler) -> None:
        """
        Remove *handler* from *event_type* subscribers.
        No-op if handler was never registered.
        """
        try:
            self._subscribers[event_type].remove(handler)
            logger.debug("Unsubscribed %s → %s", event_type, handler.__name__)
        except ValueError:
            pass

    # ── Publish ─────────────────────────────────────────────────────────────

    async def publish(
        self,
        event_type: EventType,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """
        Publish *event_type* to all registered handlers.

        Handlers are called concurrently via asyncio.gather.
        Exceptions in individual handlers are logged but do NOT propagate —
        one bad handler must not block the others.

        Parameters
        ----------
        event_type : EventType
        payload    : dict — arbitrary event data; defaults to {}
        """
        payload = payload or {}
        handlers = list(self._subscribers.get(event_type, []))
        logger.debug(
            "Publishing %s to %d handler(s): %s",
            event_type,
            len(handlers),
            payload,
        )
        if not handlers:
            return

        async def _safe_call(h: Handler) -> None:
            try:
                await h(event_type, payload)
            except Exception:
                logger.exception(
                    "Handler %s raised on event %s", h.__name__, event_type
                )

        await asyncio.gather(*(_safe_call(h) for h in handlers))

    # ── Introspection ────────────────────────────────────────────────────────

    def subscriber_count(self, event_type: EventType) -> int:
        """Return the number of handlers registered for *event_type*."""
        return len(self._subscribers.get(event_type, []))

    def all_subscriptions(self) -> dict[str, list[str]]:
        """Return a human-readable map of event → handler names (for /status)."""
        return {
            et.value: [h.__name__ for h in handlers]
            for et, handlers in self._subscribers.items()
        }


# Module-level singleton — import and use this in V2 services.
# Do NOT import from V1 code.
bus = EventBus()
