"""BBS Bot: Bollinger-Keltner Volatility Squeeze Archetype."""

from __future__ import annotations

from typing import Dict, Any, Optional
import numpy as np
import pandas as pd



from v2.core.bots.base import BotArchetype
from v2.core.types import BotName


class BBSBot(BotArchetype):
    """Bollinger-Keltner Volatility Squeeze Archetype."""

    @property
    def name(self) -> BotName:
        return BotName.BBS

    def __init__(
        self,
        bb_period: int = 20,
        bb_std: float = 2.0,
        kc_period: int = 20,
        kc_mult: float = 1.5,
        target_pct: float = 0.04,
        stop_pct: float = 0.018,
    ):

        self.bb_period = bb_period
        self.bb_std = bb_std
        self.kc_period = kc_period
        self.kc_mult = kc_mult
        self.target_pct = target_pct
        self.stop_pct = stop_pct

    def evaluate_setup(self, symbol: str, df: pd.DataFrame) -> Optional[Dict[str, Any]]:
        """Evaluates Bollinger Bands squeezing inside Keltner Channel followed by breakout."""
        if len(df) < 30:
            return None

        closes = df["close"].values
        highs = df["high"].values
        lows = df["low"].values

        # 1. Evaluate squeeze condition on prior 20-bar window [-21:-1]
        prior_closes = closes[-21:-1]
        prior_highs = highs[-21:-1]
        prior_lows = lows[-21:-1]

        sma20 = float(pd.Series(prior_closes).mean())
        std20 = float(pd.Series(prior_closes).std())

        bb_upper = sma20 + (self.bb_std * std20)
        bb_lower = sma20 - (self.bb_std * std20)

        tr = np.maximum(prior_highs - prior_lows, np.abs(prior_highs - prior_closes))
        atr20 = float(np.mean(tr))

        kc_upper = sma20 + (self.kc_mult * atr20)
        kc_lower = sma20 - (self.kc_mult * atr20)

        # Squeeze condition: BB inside KC (bb_upper <= kc_upper AND bb_lower >= kc_lower)
        is_squeezed = (bb_upper <= kc_upper) and (bb_lower >= kc_lower)
        latest_close = float(closes[-1])

        # Breakout condition: Latest close breaks above prior BB Upper or KC Upper
        if is_squeezed and latest_close > bb_upper:
            target_price = round(latest_close * (1.0 + self.target_pct), 2)
            stop_loss = round(latest_close * (1.0 - self.stop_pct), 2)

            return {
                "bot": self.name,
                "bot": self.name.value,
                "symbol": symbol,
                "direction": "BUY",
                "entry_price": latest_close,
                "stop_loss": stop_loss,
                "take_profit": target_price,
                "confluence_score": 89.0,
                "reason": "Bollinger-Keltner Volatility Squeeze Expansion Breakout",
            }

        return None

