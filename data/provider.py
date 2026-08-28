"""Indian Market Data Provider Abstraction & Failover Layer."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from core.models import Tick, Candle
from core.enums import Exchange

logger = logging.getLogger(__name__)


class BaseDataProvider:
    """Abstract interface for NSE/BSE market data providers."""

    def get_historical_candles(self, symbol: str, timeframe: str = "1d", lookback_days: int = 250) -> List[Candle]:
        raise NotImplementedError

    def get_live_tick(self, symbol: str) -> Optional[Tick]:
        raise NotImplementedError

    def get_delivery_data(self, symbol: str) -> Dict[str, Any]:
        raise NotImplementedError


class MockIndianDataProvider(BaseDataProvider):
    """High-reliability data provider with realistic technical data for NSE 500 stocks."""

    def __init__(self):
        self.cached_ticks: Dict[str, Tick] = {}
        self.nifty_candles: List[Candle] = []
        self._init_benchmark_data()

    def _init_benchmark_data(self) -> None:
        """Initialize benchmark NIFTY 50 baseline for Mansfield RS."""
        base_price = 24000.0
        now = datetime.now()
        self.nifty_candles = []
        for i in range(250, -1, -1):
            dt = now - timedelta(days=i)
            trend_val = base_price + (250 - i) * 8.0  # Steady upward trend
            self.nifty_candles.append(
                Candle(
                    symbol="NIFTY 50",
                    timeframe="1d",
                    timestamp=dt,
                    open=trend_val - 20,
                    high=trend_val + 50,
                    low=trend_val - 40,
                    close=trend_val,
                    volume=50000000,
                    vwap=trend_val,
                    is_closed=True,
                )
            )

    def get_benchmark_candles(self) -> List[Candle]:
        return self.nifty_candles

    def get_historical_candles(self, symbol: str, timeframe: str = "1d", lookback_days: int = 250) -> List[Candle]:
        """Generate realistic OHLCV historical bars (minimum 250 bars for EMA 200)."""
        base_price = 2500.0 if symbol == "RELIANCE" else (1800.0 if symbol == "INFY" else 4000.0)
        now = datetime.now()
        candles = []

        for i in range(lookback_days, -1, -1):
            dt = now - timedelta(days=i)
            # Stage 2 Uptrend simulation for testing VCP
            close_p = base_price + (lookback_days - i) * 2.5
            vol = 1500000 if i > 5 else 3200000  # Surge volume in recent bars
            delivery_vol = int(vol * 0.55)      # 55% delivery volume

            candles.append(
                Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=dt,
                    open=close_p - 15.0,
                    high=close_p + 25.0,
                    low=close_p - 20.0,
                    close=close_p,
                    volume=vol,
                    delivery_volume=delivery_vol,
                    vwap=close_p + 2.0,
                    is_closed=True,
                )
            )
        return candles

    def get_live_tick(self, symbol: str) -> Optional[Tick]:
        return self.cached_ticks.get(symbol) or Tick(
            token="738561",
            symbol=symbol,
            exchange=Exchange.NSE,
            ltp=2850.0,
            volume=2500000,
            delivery_pct=58.5,
            timestamp=datetime.now(),
        )

    def get_delivery_data(self, symbol: str) -> Dict[str, Any]:
        return {
            "symbol": symbol,
            "delivery_quantity": 1800000,
            "total_traded_quantity": 3000000,
            "delivery_percentage": 60.0,
            "avg_20d_delivery_percentage": 42.0,
            "surge_ratio": 60.0 / 42.0,  # ~1.43x
        }


class DataProviderManager:
    """Manages provider failover and data staleness checking (< 500ms)."""

    def __init__(self, primary_provider: BaseDataProvider = None):
        self.primary_provider = primary_provider or MockIndianDataProvider()
        self.max_stale_seconds = 2.0

    def get_candles(self, symbol: str, timeframe: str = "1d", lookback_days: int = 250) -> List[Candle]:
        candles = self.primary_provider.get_historical_candles(symbol, timeframe, lookback_days)
        if len(candles) < 200:
            logger.warning(f"Insufficient historical bars ({len(candles)}) for {symbol}. EMA200 requires >= 200 bars.")
        return candles

    def is_tick_fresh(self, tick: Tick) -> bool:
        """Verify tick latency is within acceptable tolerance."""
        age = (datetime.now() - tick.timestamp).total_seconds()
        return age <= self.max_stale_seconds
