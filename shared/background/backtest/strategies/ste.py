"""
7. STE (SuperTrend ATR Range Expansion) Strategy.
Vectorized implementation.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import BacktestTradeSignal, BaseStrategy


class STEStrategy(BaseStrategy):
    def __init__(self) -> None:
        super().__init__("STE (SuperTrend ATR Expansion)")

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
        ema_50 = df["ema_50"].values
        rsi = df["rsi_14"].values
        atr = df["atr_14"].values

        shift_closes = np.roll(closes, 1)
        shift_ema50 = np.roll(ema_50, 1)
        shift_atr5 = np.roll(atr, 5)

        mask = (
            (closes > ema_50)
            & (shift_closes <= shift_ema50)
            & (rsi > 55)
            & (atr > (shift_atr5 * 1.08))
        )
        cand_indices = np.where(mask)[0]

        for i in cand_indices:
            if i < 20 or i >= n - 1:
                continue

            entry = closes[i]
            stop_loss = entry - (2.5 * atr[i])
            sl_distance = entry - stop_loss
            if sl_distance <= 0:
                continue
            take_profit = entry + (sl_distance * 2.3)

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
                    metadata={"rsi": float(rsi[i]), "atr": float(atr[i])},
                )
            )

        return signals
