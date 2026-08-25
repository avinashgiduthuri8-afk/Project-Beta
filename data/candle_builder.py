"""Real-time Tick-to-Candle Resampler (1m / 5m / 15m OHLCV + Real-time VWAP)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable
from core.models import Tick, Candle

logger = logging.getLogger(__name__)


class CandleBuilder:
    """Aggregates streaming ticks into completed multi-timeframe OHLCV candles."""

    def __init__(self, timeframe_minutes: int = 5, on_candle_close: Optional[Callable[[Candle], None]] = None):
        self.timeframe_minutes = timeframe_minutes
        self.on_candle_close = on_candle_close
        self.active_candles: Dict[str, Candle] = {}
        self.candle_history: Dict[str, List[Candle]] = {}
        self.cumulative_pv: Dict[str, float] = {}   # Price * Volume for VWAP
        self.cumulative_vol: Dict[str, int] = {}    # Cumulative Volume for VWAP

    def _get_candle_slot(self, dt: datetime) -> datetime:
        """Align timestamp to the start of the timeframe period."""
        minute = (dt.minute // self.timeframe_minutes) * self.timeframe_minutes
        return dt.replace(minute=minute, second=0, microsecond=0)

    def process_tick(self, tick: Tick) -> Optional[Candle]:
        """Process incoming tick and return closed candle if period completed."""
        symbol = tick.symbol
        slot = self._get_candle_slot(tick.timestamp)

        # Update VWAP accumulators
        self.cumulative_pv[symbol] = self.cumulative_pv.get(symbol, 0.0) + (tick.ltp * tick.volume)
        self.cumulative_vol[symbol] = self.cumulative_vol.get(symbol, 0) + tick.volume
        total_vol = self.cumulative_vol[symbol]
        current_vwap = (self.cumulative_pv[symbol] / total_vol) if total_vol > 0 else tick.ltp

        closed_candle = None

        if symbol in self.active_candles:
            current_candle = self.active_candles[symbol]

            # Check if active candle interval has finished
            if slot > current_candle.timestamp:
                current_candle.is_closed = True
                closed_candle = current_candle

                if symbol not in self.candle_history:
                    self.candle_history[symbol] = []
                self.candle_history[symbol].append(current_candle)

                if self.on_candle_close:
                    self.on_candle_close(current_candle)

                # Initialize new candle
                self.active_candles[symbol] = Candle(
                    symbol=symbol,
                    timeframe_minutes=self.timeframe_minutes,
                    timestamp=slot,
                    open=tick.ltp,
                    high=tick.ltp,
                    low=tick.ltp,
                    close=tick.ltp,
                    volume=tick.volume,
                    vwap=current_vwap,
                    is_closed=False,
                )
            else:
                # Update existing candle
                current_candle.high = max(current_candle.high, tick.ltp)
                current_candle.low = min(current_candle.low, tick.ltp)
                current_candle.close = tick.ltp
                current_candle.volume += tick.volume
                current_candle.vwap = current_vwap
        else:
            # Initialize first candle
            self.active_candles[symbol] = Candle(
                symbol=symbol,
                timeframe_minutes=self.timeframe_minutes,
                timestamp=slot,
                open=tick.ltp,
                high=tick.ltp,
                low=tick.ltp,
                close=tick.ltp,
                volume=tick.volume,
                vwap=current_vwap,
                is_closed=False,
            )

        return closed_candle

    def get_history(self, symbol: str) -> List[Candle]:
        return self.candle_history.get(symbol, [])
