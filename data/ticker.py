"""WebSocket Ticker Stream Consumer with Auto-Reconnect Exponential Backoff."""

from __future__ import annotations

import logging
import time
import threading
from typing import List, Callable, Dict, Any, Optional
from core.models import Tick
from core.enums import Exchange
from data.event_bus import EventBus

logger = logging.getLogger(__name__)


class WebSocketTicker:
    """Consumer for live broker WebSocket market quotes with backoff reconnection."""

    def __init__(self, event_bus: EventBus, tokens: Optional[List[str]] = None):
        self.event_bus = event_bus
        self.tokens = tokens or []
        self.is_running = False
        self.worker_thread: Optional[threading.Thread] = None
        self.reconnect_delay = 1.0
        self.max_reconnect_delay = 30.0

    def subscribe_tokens(self, tokens: List[str]) -> None:
        """Add instrument tokens to subscribe."""
        for token in tokens:
            if token not in self.tokens:
                self.tokens.append(token)

    def start(self) -> None:
        """Start the background ticker ingestion loop."""
        if self.is_running:
            return
        self.is_running = True
        self.worker_thread = threading.Thread(target=self._run_loop, daemon=True)
        self.worker_thread.start()
        logger.info(f"WebSocket Ticker started with {len(self.tokens)} instrument subscriptions.")

    def stop(self) -> None:
        """Stop ticker stream."""
        self.is_running = False
        if self.worker_thread and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=1.0)
        logger.info("WebSocket Ticker stopped.")

    def _run_loop(self) -> None:
        """Mock stream consumer / live connection loop with auto-reconnect."""
        while self.is_running:
            try:
                # In live mode: connect to broker websocket (e.g. KiteTicker, SmartWebSocket)
                time.sleep(0.5)
                # Reset reconnect delay on successful run
                self.reconnect_delay = 1.0
            except Exception as e:
                logger.error(f"WebSocket disconnected: {e}. Reconnecting in {self.reconnect_delay:.1f}s...")
                time.sleep(self.reconnect_delay)
                self.reconnect_delay = min(self.reconnect_delay * 2, self.max_reconnect_delay)

    def inject_tick(self, tick: Tick) -> None:
        """Simulate or inject incoming tick and publish to event bus."""
        self.event_bus.publish("market.tick", tick)
