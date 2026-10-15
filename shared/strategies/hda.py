"""
HDA (High Delivery & CVD Absorption) Strategy for Indian Equities.
Detects heavy institutional accumulation evidenced by surging cumulative volume delta and relative volume.
"""

from __future__ import annotations

from decimal import Decimal
import numpy as np
import pandas as pd

from market.symbols import StockSymbol
from .base import BaseStockStrategy, StrategySignal


class HDAStrategy(BaseStockStrategy):
    def __init__(self) -> None:
        super().__init__("HDA")

    def evaluate(self, df: pd.DataFrame, symbol: StockSymbol) -> StrategySignal | None:
        n = len(df)
        if n < 40:
            return None

        closes = df["close"].values
        highs = df["high"].values
        lows = df["low"].values
        cvd = df["cvd"].values
        rvol = df["rvol"].values

        i = n - 1
        cvd_roll_20_max = np.max(cvd[i - 20 : i])
        high_roll_15_max = np.max(highs[i - 15 : i])

        is_accumulation = (cvd[i] > cvd_roll_20_max) and (closes[i] > high_roll_15_max) and (rvol[i] >= 1.65)
        if "ema_50" in df.columns and not pd.isna(df["ema_50"].iloc[i]):
            is_accumulation = is_accumulation and (closes[i] >= df["ema_50"].iloc[i])

        if not is_accumulation:
            return None

        entry = Decimal(str(round(closes[i], 2)))
        recent_low = float(np.min(lows[i - 5 : i])) * 0.995
        min_sl = float(entry) * 0.985
        stop_loss_val = min(recent_low, min_sl)
        stop_loss = Decimal(str(round(stop_loss_val, 2)))

        sl_distance = entry - stop_loss
        if sl_distance <= 0:
            return None
        take_profit = Decimal(str(round(float(entry) + (float(sl_distance) * 2.4), 2)))

        score = 91 if rvol[i] >= 2.2 else 87

        return StrategySignal(
            strategy_name=self.name,
            symbol=symbol,
            entry_price=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            score=score,
            metadata={"rvol": float(rvol[i]), "cvd": float(cvd[i])},
        )
