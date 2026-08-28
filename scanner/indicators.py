"""High-Precision Technical Indicators for Indian Equities (Project-Beta)."""

from __future__ import annotations

import math
from typing import List, Optional
from core.models import Candle


class Indicators:
    """Mathematical indicator suite ensuring correct lookback lengths and no lookahead bias."""

    @staticmethod
    def calculate_ema(candles: List[Candle], period: int = 20) -> float:
        """Calculate Exponential Moving Average (EMA) with minimum lookback validation."""
        if not candles or len(candles) < period:
            return candles[-1].close if candles else 0.0

        multiplier = 2.0 / (period + 1)
        # Seed with initial SMA
        ema = sum(c.close for c in candles[:period]) / period
        for c in candles[period:]:
            ema = (c.close - ema) * multiplier + ema
        return round(ema, 2)

    @staticmethod
    def calculate_sma(values: List[float], period: int) -> float:
        if not values or len(values) < period:
            return values[-1] if values else 0.0
        return sum(values[-period:]) / period

    @staticmethod
    def calculate_atr(candles: List[Candle], period: int = 14) -> float:
        """Average True Range (ATR) for volatility and stop-loss sizing."""
        if not candles or len(candles) < 2:
            return 1.0

        true_ranges = []
        for i in range(1, len(candles)):
            high = candles[i].high
            low = candles[i].low
            prev_close = candles[i - 1].close
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            true_ranges.append(tr)

        if len(true_ranges) < period:
            return round(sum(true_ranges) / len(true_ranges), 2)

        # Wilder's smoothing
        atr = sum(true_ranges[:period]) / period
        for tr in true_ranges[period:]:
            atr = (atr * (period - 1) + tr) / period
        return round(atr, 2)

    @staticmethod
    def calculate_rsi(candles: List[Candle], period: int = 14) -> float:
        """Relative Strength Index (RSI 14)."""
        if not candles or len(candles) <= period:
            return 50.0

        gains, losses = [], []
        for i in range(1, len(candles)):
            diff = candles[i].close - candles[i - 1].close
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))

        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return round(rsi, 2)

    @staticmethod
    def calculate_mansfield_rs(stock_candles: List[Candle], benchmark_candles: List[Candle], period: int = 50) -> float:
        """
        Mansfield Relative Strength vs Benchmark (e.g. NIFTY 50):
        RS_Base = Stock_Close / Index_Close
        Mansfield_RS = ((RS_Base / SMA_period(RS_Base)) - 1.0) * 100.0
        Positive values indicate true alpha outperformance.
        """
        min_len = min(len(stock_candles), len(benchmark_candles))
        if min_len < period:
            return 0.0

        ratios = [
            stock_candles[-min_len + i].close / benchmark_candles[-min_len + i].close
            for i in range(min_len)
        ]

        sma_ratio = sum(ratios[-period:]) / period
        if sma_ratio == 0:
            return 0.0

        current_ratio = ratios[-1]
        mansfield_score = ((current_ratio / sma_ratio) - 1.0) * 100.0
        return round(mansfield_score, 2)
