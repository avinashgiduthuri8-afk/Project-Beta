"""
2. NR7 (Narrow Range 7 Squeeze) Strategy.
Vectorized implementation.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import BacktestTradeSignal, BaseStrategy


class NR7Strategy(BaseStrategy):
    def __init__(self) -> None:
        super().__init__("NR7 (Narrow Range 7 Squeeze)")

    def generate_signals(
        self,
        df: pd.DataFrame,
        pair: str = "BTC/USDT",
        timeframe: str = "1H",
    ) -> list[BacktestTradeSignal]:
        signals: list[BacktestTradeSignal] = []
        n = len(df)
        if n < 30:
            return signals

        closes = df["close"].values
        highs = df["high"].values
        lows = df["low"].values
        is_nr7 = df["is_nr7"].values
        atr = df["atr_14"].values
        ema_21 = df["ema_21"].values

        # Mask: Prior bar was NR7 and current bar breaks prior high
        shifted_nr7 = np.roll(is_nr7, 1)
        shifted_nr7[0] = False
        shifted_highs = np.roll(highs, 1)

        mask = shifted_nr7 & (closes > shifted_highs) & (closes > ema_21)
        cand_indices = np.where(mask)[0]

        for i in cand_indices:
            if i < 10 or i >= n - 1:
                continue

            nr7_high = highs[i - 1]
            nr7_low = lows[i - 1]
            atr_val = atr[i]

            if (highs[i] - lows[i]) >= (1.1 * atr_val):
                entry = closes[i]
                stop_loss = nr7_low * 0.997
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
                        metadata={
                            "nr7_range": float(nr7_high - nr7_low),
                            "atr": float(atr_val),
                        },
                    )
                )

        return signals
