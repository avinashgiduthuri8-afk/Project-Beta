"""
1. VCP (Minervini Volatility Contraction Pattern) Strategy.
Vectorized boolean masking for high-speed execution.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import BacktestTradeSignal, BaseStrategy


class VCPStrategy(BaseStrategy):
    def __init__(self) -> None:
        super().__init__("VCP (Volatility Contraction Pattern)")

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
        highs = df["high"].values
        lows = df["low"].values
        rvol = df["rvol"].values
        ema_50 = df["ema_50"].values
        donchian_high = df["donchian_high_20"].values

        # Vectorized candidate selection
        cand_mask = (closes > ema_50) & (rvol >= 1.7) & (closes > donchian_high)
        cand_indices = np.where(cand_mask)[0]

        for i in cand_indices:
            if i < 30 or i >= n - 1:
                continue

            pivot_res = np.max(highs[i - 20 : i])
            if closes[i] <= pivot_res:
                continue

            t1 = highs[i - 20 : i - 14].max() - lows[i - 20 : i - 14].min()
            t2 = highs[i - 14 : i - 7].max() - lows[i - 14 : i - 7].min()
            t3 = highs[i - 7 : i].max() - lows[i - 7 : i].min()

            if not (t1 >= t2 >= t3 > 0):
                continue

            entry = closes[i]
            stop_loss = np.min(lows[i - 5 : i]) * 0.995
            sl_distance = entry - stop_loss
            if sl_distance <= 0:
                continue
            take_profit = entry + (sl_distance * 2.5)

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
                    metadata={"t1": float(t1), "t2": float(t2), "t3": float(t3)},
                )
            )

        return signals
