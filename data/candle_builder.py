"""
Live Tick to OHLCV Candle Aggregator / Resampler.
Builds real-time 1m, 3m, 5m, and 15m candles from incoming live ticks.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional
from core.models import Candle, Tick


class CandleBuilder:
    """
    Maintains active in-progress candles per symbol and dispatches completed candles.
    """

    def __init__(
        self,
        timeframe_minutes: int = 1,
        on_candle_closed: Optional[Callable[[Candle], None]] = None,
        on_candle_update: Optional[Callable[[Candle], None]] = None,
    ) -> None:
        self.timeframe_minutes = timeframe_minutes
        self.timeframe_str = f"{timeframe_minutes}m"
        self.on_candle_closed = on_candle_closed
        self.on_candle_update = on_candle_update
        self._active_candles: Dict[str, Candle] = {}
        self._history: Dict[str, List[Candle]] = {}

    def _get_candle_interval(self, dt: datetime) -> tuple[datetime, datetime]:
        """Align timestamp to the start of current timeframe block."""
        minute = (dt.minute // self.timeframe_minutes) * self.timeframe_minutes
        start = dt.replace(minute=minute, second=0, microsecond=0)
        end = start + timedelta(minutes=self.timeframe_minutes)
        return start, end

    def process_tick(self, tick: Tick) -> None:
        """
        Process an incoming live tick and update or close the current candle.
        """
        symbol = tick.symbol
        price = tick.last_price
        volume = tick.last_quantity or 1
        tick_time = tick.timestamp

        start_time, end_time = self._get_candle_interval(tick_time)

        active = self._active_candles.get(symbol)

        # Check if active candle needs closing (time elapsed into next bucket)
        if active and tick_time >= active.end_time:
            active.is_closed = True
            if symbol not in self._history:
                self._history[symbol] = []
            self._history[symbol].append(active)

            if self.on_candle_closed:
                self.on_candle_closed(active)

            # Start fresh candle
            active = None

        if active is None:
            # Create new candle
            active = Candle(
                symbol=symbol,
                exchange=tick.exchange,
                instrument_token=tick.instrument_token,
                timeframe=self.timeframe_str,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=volume,
                start_time=start_time,
                end_time=end_time,
                is_closed=False,
            )
            self._active_candles[symbol] = active
        else:
            # Update existing active candle
            active.high = max(active.high, price)
            active.low = min(active.low, price)
            active.close = price
            active.volume += volume

        if self.on_candle_update:
            self.on_candle_update(active)

    def get_history(self, symbol: str) -> List[Candle]:
        """Get closed candle history for a symbol."""
        return self._history.get(symbol, [])

    def get_active_candle(self, symbol: str) -> Optional[Candle]:
        """Get currently forming active candle."""
        return self._active_candles.get(symbol)
