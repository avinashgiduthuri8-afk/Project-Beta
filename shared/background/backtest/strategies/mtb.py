"""
4. MTB (Momentum Trend Breakout) Strategy.
Vectorized implementation.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import BacktestTradeSignal, BaseStrategy


class MTBStrategy(BaseStrategy):
    def __init__(self) -> None:
        super().__init__("MTB (Momentum Trend Breakout)")

    def generate_signals(
        self,
        df: pd.DataFrame,
        pair: str = "BTC/USDT",
        timeframe: str = "1H",
    ) -> list[BacktestTradeSignal]:
        signals: list[BacktestTradeSignal] = []
        n = len(df)
        if n < 50:
            return signals

        closes = df["close"].values
        donchian_high = df["donchian_high_20"].values
        ema_50 = df["ema_50"].values
        ema_200 = df["ema_200"].values
        macd_hist = df["macd_hist"].values

        mask = (
            (ema_50 > ema_200)
            & (closes > ema_50)
            & (closes > donchian_high)
            & (macd_hist > 0)
        )
        cand_indices = np.where(mask)[0]

        for i in cand_indices:
            if i < 25 or i >= n - 1:
                continue

            entry = closes[i]
            stop_loss = entry * 0.980  # 2.0% SL
            take_profit = entry * 1.045  # 4.5% TP

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
                    metadata={
                        "donchian_high": float(donchian_high[i]),
                        "macd_hist": float(macd_hist[i]),
                    },
                )
            )

        return signals
