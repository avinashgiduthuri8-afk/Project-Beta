"""
BBS (Bollinger Band Squeeze Breakout) Strategy for Indian Equities.
Detects low-volatility compression (Bollinger Bands contracted within Keltner Channels)
followed by an explosive directional breakout with expanding volume.
"""

from __future__ import annotations

from decimal import Decimal
import pandas as pd

from market.symbols import StockSymbol
from .base import BaseStockStrategy, StrategySignal


class BBSStrategy(BaseStockStrategy):
    def __init__(self) -> None:
        super().__init__("BBS")

    def evaluate(self, df: pd.DataFrame, symbol: StockSymbol) -> StrategySignal | None:
        n = len(df)
        if n < 40:
            return None

        closes = df["close"].values
        bb_upper = df["bb_upper"].values
        bb_lower = df["bb_lower"].values
        kc_upper = df["kc_upper"].values
        kc_lower = df["kc_lower"].values
        rvol = df["rvol"].values

        i = n - 1
        # Check if previous bar was inside squeeze
        was_squeezed = (bb_upper[i - 1] <= kc_upper[i - 1]) and (bb_lower[i - 1] >= kc_lower[i - 1])
        # Current bar breaks out above upper Bollinger Band with volume
        is_breakout = was_squeezed and (closes[i] > bb_upper[i]) and (rvol[i] >= 1.4)

        if not is_breakout:
            return None

        entry = Decimal(str(round(closes[i], 2)))
        stop_loss_val = float(bb_lower[i - 1])
        stop_loss = Decimal(str(round(stop_loss_val, 2)))

        sl_distance = entry - stop_loss
        if sl_distance <= 0:
            return None
        take_profit = Decimal(str(round(float(entry) + (float(sl_distance) * 2.4), 2)))

        score = 90 if rvol[i] >= 1.8 else 86

        return StrategySignal(
            strategy_name=self.name,
            symbol=symbol,
            entry_price=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            score=score,
            metadata={"bb_upper": float(bb_upper[i]), "kc_upper": float(kc_upper[i]), "rvol": float(rvol[i])},
        )
