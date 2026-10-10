"""
9. MRB (Statistical Mean Reversion) Strategy.
Vectorized implementation.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import BacktestTradeSignal, BaseStrategy


class MRBStrategy(BaseStrategy):
    def __init__(self) -> None:
        super().__init__("MRB (Statistical Mean Reversion)")

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

        opens = df["open"].values
        closes = df["close"].values
        highs = df["high"].values
        lows = df["low"].values
        vwap = df["vwap"].values
        rsi = df["rsi_14"].values
        bb_lower = df["bb_lower"].values

        candle_len = highs - lows
        lower_shadow = np.minimum(opens, closes) - lows
        wick_ratio = np.where(candle_len > 0, lower_shadow / candle_len, 0.0)

        mask = (
            ((lows < bb_lower) | (lows < (vwap * 0.96)))
            & (rsi < 35)
            & (wick_ratio >= 0.40)
        )
        cand_indices = np.where(mask)[0]

        for i in cand_indices:
            if i < 25 or i >= n - 1:
                continue

            entry = closes[i]
            stop_loss = lows[i] * 0.995
            sl_distance = entry - stop_loss
            if sl_distance <= 0:
                continue
            take_profit = vwap[i]

            if take_profit > entry:
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
                        metadata={"rsi": float(rsi[i]), "vwap": float(vwap[i])},
                    )
                )

        return signals
