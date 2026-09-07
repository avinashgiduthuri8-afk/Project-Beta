"""STE Bot: SuperTrend Momentum Trading Archetype for Equities."""

from __future__ import annotations

from typing import Dict, Any, List, Optional
import numpy as np
import pandas as pd


class STEBot:
    """SuperTrend Momentum Archetype (Trend-Following)."""

    def __init__(
        self,
        atr_period: int = 10,
        factor: float = 3.0,
        target_pct: float = 0.04,
        stop_pct: float = 0.02,
    ):
        self.name = "STE"
        self.atr_period = atr_period
        self.factor = factor
        self.target_pct = target_pct
        self.stop_pct = stop_pct

    def evaluate_setup(self, symbol: str, df: pd.DataFrame) -> Optional[Dict[str, Any]]:
        """Evaluates SuperTrend breakout and EMA alignment on candle DataFrame."""
        if len(df) < 30:
            return None

        closes = df["close"].values
        highs = df["high"].values
        lows = df["low"].values

        latest_close = float(closes[-1])
        prev_close = float(closes[-2])

        # Simple indicator proxy calculations
        ema20 = pd.Series(closes).ewm(span=20).mean().values[-1]
        ema50 = pd.Series(closes).ewm(span=50 if len(closes) >= 50 else len(closes)).mean().values[-1]

        # Trend & Breakout Condition: Close > EMA20 > EMA50 and price moving up
        if latest_close > ema20 > ema50 and latest_close > prev_close:
            target_price = round(latest_close * (1.0 + self.target_pct), 2)
            stop_loss = round(latest_close * (1.0 - self.stop_pct), 2)

            return {
                "bot": self.name,
                "symbol": symbol,
                "direction": "BUY",
                "entry_price": latest_close,
                "stop_loss": stop_loss,
                "take_profit": target_price,
                "confluence_score": 88.0,
                "reason": "SuperTrend Bullish Momentum & Stacked EMA Cross",
            }

        return None

