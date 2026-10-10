"""
6. HDA (High Delivery & CVD Volume Absorption) Strategy.
Vectorized implementation.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import BacktestTradeSignal, BaseStrategy


class HDAStrategy(BaseStrategy):
    def __init__(self) -> None:
        super().__init__("HDA (High Delivery & CVD Absorption)")

    def generate_signals(
        self,
        df: pd.DataFrame,
        pair: str = "BTC/USDT",
        timeframe: str = "1H",
    ) -> list[BacktestTradeSignal]:
        signals: list[BacktestTradeSignal] = []
        n = len(df)
        if n < 40:
            return signals

        closes = df["close"].values
        highs = df["high"].values
        lows = df["low"].values
        cvd = df["cvd"].values
        rvol = df["rvol"].values

        # Rolling 20 max CVD and rolling 15 high
        cvd_roll_20 = pd.Series(cvd).rolling(20).max().shift(1).values
        high_roll_15 = pd.Series(highs).rolling(15).max().shift(1).values

        mask = (cvd > cvd_roll_20) & (closes > high_roll_15) & (rvol >= 1.65)
        cand_indices = np.where(mask)[0]

        for i in cand_indices:
            if i < 20 or i >= n - 1:
                continue

            entry = closes[i]
            stop_loss = np.min(lows[i - 5 : i]) * 0.995
            sl_distance = entry - stop_loss
            if sl_distance <= 0:
                continue
            take_profit = entry + (sl_distance * 2.4)

            signals.append(
                BacktestTradeSignal(
                    strategy_name=self.name,
                    pair=pair,
                    timeframe=timeframe,
                    bar_index=int(i),
                    entry_price=float(entry),
                    stop_loss_price=float(stop_loss),
                    take_profit_price=float(take_profit),
                    direction="LONG",
                    metadata={"cvd": float(cvd[i]), "rvol": float(rvol[i])},
                )
            )

        return signals
