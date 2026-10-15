"""
VCP (Volatility Contraction Pattern) Strategy for Indian Equities.
Detects progressive range tightening across multiple contraction waves (T1 >= T2 >= T3)
followed by a high-volume Donchian pivot breakout.
"""

from __future__ import annotations

from decimal import Decimal
import numpy as np
import pandas as pd

from market.symbols import StockSymbol
from .base import BaseStockStrategy, StrategySignal


class VCPStrategy(BaseStockStrategy):
    def __init__(self) -> None:
        super().__init__("VCP")

    def evaluate(self, df: pd.DataFrame, symbol: StockSymbol) -> StrategySignal | None:
        n = len(df)
        if n < 50:
            return None

        closes = df["close"].values
        highs = df["high"].values
        lows = df["low"].values
        rvol = df["rvol"].values
        ema_50 = df["ema_50"].values
        donchian_high = df["donchian_high_20"].values

        i = n - 1
        is_candidate = (
            (closes[i] > ema_50[i])
            and (rvol[i] >= 1.5)
            and (closes[i] > donchian_high[i])
        )
        if not is_candidate:
            return None

        pivot_res = np.max(highs[i - 20 : i])
        if closes[i] <= pivot_res:
            return None

        t1 = np.max(highs[i - 20 : i - 14]) - np.min(lows[i - 20 : i - 14])
        t2 = np.max(highs[i - 14 : i - 7]) - np.min(lows[i - 14 : i - 7])
        t3 = np.max(highs[i - 7 : i]) - np.min(lows[i - 7 : i])

        if not (t1 >= t2 >= t3 > 0):
            return None

        entry = Decimal(str(round(closes[i], 2)))
        stop_loss_val = float(np.min(lows[i - 5 : i])) * 0.995
        stop_loss = Decimal(str(round(stop_loss_val, 2)))

        sl_distance = entry - stop_loss
        if sl_distance <= 0:
            return None
        take_profit = Decimal(str(round(float(entry) + (float(sl_distance) * 2.5), 2)))

        score = 93 if rvol[i] >= 2.0 else 89

        return StrategySignal(
            strategy_name=self.name,
            symbol=symbol,
            entry_price=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            score=score,
            metadata={"t1": float(t1), "t2": float(t2), "t3": float(t3), "rvol": float(rvol[i])},
        )
