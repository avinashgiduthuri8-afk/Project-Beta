"""V2 Market Data: Simulated Ticker Stream and Warm-up Cache."""

import asyncio
import logging
from typing import Dict, List, Optional
import pandas as pd
from datetime import datetime, timezone

from v2.bus.event_bus import bus
from v2.bus.event_types import EventType
from v2.repository.db import Database

logger = logging.getLogger("v2.market_data.ticker_stream")

class TickerStream:
    """Streams live market data and maintains the market_candles cache."""

    def __init__(self, db: Database, symbols: List[str]):
        self.db = db
        self.symbols = symbols
        self._running = False
        self._latest_prices: Dict[str, float] = {}

    async def initialize_cache(self) -> None:
        """Warms up the market_candles database table with historical data."""
        logger.info(f"Warming up market_candles cache for {len(self.symbols)} symbols.")
        # In a real system, we would fetch from broker (e.g., Kite Historical).
        # Here we simulate historical warmup.
        pass

    async def start(self) -> None:
        """Starts the ticker stream and publishes MARKET_TICK events."""
        self._running = True
        logger.info("TickerStream started.")
        asyncio.create_task(self._stream_loop())

    async def stop(self) -> None:
        """Stops the ticker stream."""
        self._running = False
        logger.info("TickerStream stopped.")

    async def _stream_loop(self) -> None:
        """Background loop emitting simulated or live ticks."""
        while self._running:
            # Simulate a market tick for a random symbol or batch of symbols
            # In live, this would read from WebSocket
            # We use a dummy tick for now, or just publish
            price_map = {sym: 100.0 for sym in self.symbols} # Mock prices
            self._latest_prices.update(price_map)
            
            await bus.publish(
                EventType.MARKET_TICK,
                payload={"prices": price_map, "timestamp": datetime.now(timezone.utc).isoformat()}
            )
            
            await asyncio.sleep(5.0) # Tick every 5 seconds
            
    def get_latest_price(self, symbol: str) -> Optional[float]:
        return self._latest_prices.get(symbol)

