"""
NR7 (Narrow Range 7 Squeeze Breakout) Strategy for Indian Equities.
Classic Toby Crabel volatility contraction setup: triggers when a stock breaks above
the high of the narrowest range bar of the past 7 periods with volume and trend confirmation.
"""

from __future__ import annotations

from decimal import Decimal
import pandas as pd

from market.symbols import StockSymbol
from .base import BaseStockStrategy, StrategySignal


class NR7Strategy(BaseStockStrategy):
    def __init__(self) -> None:
        super().__init__("NR7")

    def evaluate(self, df: pd.DataFrame, symbol: StockSymbol) -> StrategySignal | None:
        n = len(df)
        if n < 30:
            return None

        closes = df["close"].values
        highs = df["high"].values
        lows = df["low"].values
        is_nr7 = df["is_nr7"].values
        atr = df["atr_14"].values
        ema_21 = df["ema_21"].values
        rvol = df["rvol"].values

        i = n - 1
        # Prior bar was NR7 and current bar breaks out above its high
        prior_was_nr7 = bool(is_nr7[i - 1])
        breaks_prior_high = closes[i] > highs[i - 1]
        above_ema = closes[i] > ema_21[i]
        atr_expansion = (highs[i] - lows[i]) >= (1.0 * atr[i]) if not pd.isna(atr[i]) else True

        if not (prior_was_nr7 and breaks_prior_high and above_ema and atr_expansion):
            return None

        entry = Decimal(str(round(closes[i], 2)))
        stop_loss_val = float(lows[i - 1]) * 0.997
        stop_loss = Decimal(str(round(stop_loss_val, 2)))

        sl_distance = entry - stop_loss
        if sl_distance <= 0:
            return None
        take_profit = Decimal(str(round(float(entry) + (float(sl_distance) * 2.3), 2)))

        score = 90 if rvol[i] >= 1.5 else 86

        return StrategySignal(
            strategy_name=self.name,
            symbol=symbol,
            entry_price=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            score=score,
            metadata={"nr7_high": float(highs[i - 1]), "nr7_low": float(lows[i - 1]), "rvol": float(rvol[i])},
        )
