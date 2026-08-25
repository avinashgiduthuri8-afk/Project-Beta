"""
Order Book Syncer.
Periodically polls broker's order book and reconciles in-memory state.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Optional
from core.interfaces import BaseBroker
from oms.order_manager import OrderManager

logger = logging.getLogger(__name__)


class OrderBookSyncer:
    """
    Background sync worker that queries the broker's order book every N seconds
    and notifies OrderManager of updates/fills.
    """

    def __init__(
        self,
        broker: BaseBroker,
        order_manager: OrderManager,
        interval_sec: float = 5.0,
    ) -> None:
        self.broker = broker
        self.order_manager = order_manager
        self.interval_sec = interval_sec
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start background synchronization thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="OrderBookSyncer")
        self._thread.start()
        logger.info(f"OrderBookSyncer started (interval={self.interval_sec}s).")

    def stop(self) -> None:
        """Stop background synchronization."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        logger.info("OrderBookSyncer stopped.")

    def sync_once(self) -> None:
        """Perform a single immediate synchronization."""
        try:
            broker_orders = self.broker.get_order_book()
            for remote_order in broker_orders:
                self.order_manager.update_order_state(remote_order)
        except Exception as e:
            logger.error(f"Error during order book sync: {e}")

    def _run_loop(self) -> None:
        while self._running:
            self.sync_once()
            time.sleep(self.interval_sec)
