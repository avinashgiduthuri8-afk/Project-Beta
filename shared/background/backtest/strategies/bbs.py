"""
8. BBS (Bollinger Band Squeeze Breakout) Strategy.
Vectorized implementation.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import BacktestTradeSignal, BaseStrategy


class BBSStrategy(BaseStrategy):
    def __init__(self) -> None:
        super().__init__("BBS (Bollinger Band Squeeze)")

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
        bb_upper = df["bb_upper"].values
        bb_lower = df["bb_lower"].values
        kc_upper = df["kc_upper"].values
        kc_lower = df["kc_lower"].values
        rvol = df["rvol"].values

        shift_bbu = np.roll(bb_upper, 1)
        shift_bbl = np.roll(bb_lower, 1)
        shift_kcu = np.roll(kc_upper, 1)
        shift_kcl = np.roll(kc_lower, 1)

        was_squeezed = (shift_bbu <= shift_kcu) & (shift_bbl >= shift_kcl)
        is_breakout = was_squeezed & (closes > bb_upper) & (rvol >= 1.4)
        cand_indices = np.where(is_breakout)[0]

        for i in cand_indices:
            if i < 25 or i >= n - 1:
                continue

            entry = closes[i]
            stop_loss = bb_lower[i - 1]
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
                    metadata={
                        "bb_upper": float(bb_upper[i]),
                        "kc_upper": float(kc_upper[i]),
                    },
                )
            )

        return signals
