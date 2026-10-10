"""
3. PPA (Pocket Pivot Accumulation) Strategy.
Vectorized implementation.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import BacktestTradeSignal, BaseStrategy


class PPAStrategy(BaseStrategy):
    def __init__(self) -> None:
        super().__init__("PPA (Pocket Pivot Accumulation)")

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

        opens = df["open"].values
        closes = df["close"].values
        lows = df["low"].values
        volumes = df["volume"].values
        ema_9 = df["ema_9"].values
        ema_50 = df["ema_50"].values

        # Up candle crossing EMA 9 while above EMA 50
        cross_ema9 = (closes > ema_9) & (np.roll(closes, 1) <= np.roll(ema_9, 1))
        uptrend = (closes > opens) & (closes > ema_50) & cross_ema9
        cand_indices = np.where(uptrend)[0]

        for i in cand_indices:
            if i < 12 or i >= n - 1:
                continue

            down_volumes = [
                volumes[k] for k in range(i - 10, i) if closes[k] < opens[k]
            ]
            max_down_vol = max(down_volumes) if down_volumes else 0.0

            if volumes[i] > max_down_vol:
                entry = closes[i]
                stop_loss = np.min(lows[i - 3 : i + 1]) * 0.995
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
                            "up_vol": float(volumes[i]),
                            "max_down_vol": float(max_down_vol),
                        },
                    )
                )

        return signals
