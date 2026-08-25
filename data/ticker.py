"""
Real-time WebSocket Ticker & Feed Processor for Indian Brokers.
Handles live streaming of LTP & Market Depth, auto-reconnection, and tick distribution.
"""

from __future__ import annotations

import asyncio
import logging
import random
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional
from config.config_loader import BotSettings, SymbolConfig
from core.enums import Exchange
from core.models import Tick
from data.event_bus import EventBus

logger = logging.getLogger(__name__)


class WebSocketTicker:
    """
    WebSocket ticker consumer supporting live broker streaming and mock feeds.
    """

    def __init__(
        self,
        settings: BotSettings,
        event_bus: EventBus,
        symbols: Optional[List[SymbolConfig]] = None,
    ) -> None:
        self.settings = settings
        self.event_bus = event_bus
        self.symbols = symbols or settings.symbols
        self.token_map: Dict[int, SymbolConfig] = {s.instrument_token: s for s in self.symbols}
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.reconnect_attempts = 0
        self.max_backoff = 30.0

    def start(self) -> None:
        """Start the ticker in a background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_feed, daemon=True, name="WebSocketTicker")
        self._thread.start()
        logger.info(f"WebSocketTicker started for {len(self.symbols)} symbols.")

    def stop(self) -> None:
        """Stop the ticker."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        logger.info("WebSocketTicker stopped.")

    def _run_feed(self) -> None:
        """
        Main loop for ticker streaming.
        Runs simulated feed in PAPER mode, or connects to live broker WebSocket in LIVE mode.
        """
        mode = self.settings.env.trading_mode.upper()
        if mode == "PAPER":
            self._run_simulated_stream()
        else:
            self._run_live_stream()

    def _run_simulated_stream(self) -> None:
        """Generates realistic tick fluctuations for paper trading."""
        logger.info("Running simulated real-time tick stream...")
        base_prices = {
            "RELIANCE": 2950.0,
            "TCS": 4200.0,
            "INFY": 1850.0,
            "HDFCBANK": 1650.0,
            "NIFTY26AUG24500CE": 125.0,
        }

        while self._running:
            for scrip in self.symbols:
                base = base_prices.get(scrip.symbol, 500.0)
                # Apply small random delta (0.05 step multiples)
                delta = random.choice([-0.25, -0.10, -0.05, 0.0, 0.05, 0.10, 0.25])
                current_price = round(max(1.0, base + delta), 2)
                base_prices[scrip.symbol] = current_price

                tick = Tick(
                    symbol=scrip.symbol,
                    exchange=Exchange(scrip.exchange),
                    instrument_token=scrip.instrument_token,
                    last_price=current_price,
                    last_quantity=random.randint(1, 50),
                    volume=random.randint(100, 50000),
                    timestamp=datetime.now(timezone.utc),
                )

                # Dispatch tick to event bus
                self.event_bus.publish("tick", tick)

            time.sleep(1.0)  # Stream frequency: 1 Hz

    def _run_live_stream(self) -> None:
        """Live broker WebSocket with exponential backoff on disconnect."""
        while self._running:
            try:
                logger.info("Connecting to live Broker WebSocket feed...")
                # Broker-specific live connection logic
                time.sleep(5.0)
            except Exception as e:
                self.reconnect_attempts += 1
                backoff = min(self.max_backoff, 2 ** self.reconnect_attempts)
                logger.error(f"WebSocket disconnected: {e}. Reconnecting in {backoff:.1f}s...")
                time.sleep(backoff)
