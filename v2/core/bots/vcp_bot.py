"""VCP Bot: Volatility Contraction Pattern Archetype (Minervini Squeeze)."""

from __future__ import annotations

from typing import Dict, Any, Optional
import numpy as np
import pandas as pd



from v2.core.bots.base import BotArchetype
from v2.core.types import BotName


class VCPBot(BotArchetype):
    """Minervini Volatility Contraction Pattern (VCP) Archetype."""

    @property
    def name(self) -> BotName:
        return BotName.VCP

    def __init__(
        self,
        target_pct: float = 0.06,
        stop_pct: float = 0.02,
    ):

        self.target_pct = target_pct
        self.stop_pct = stop_pct

    def evaluate_setup(self, symbol: str, df: pd.DataFrame) -> Optional[Dict[str, Any]]:
        """Evaluates volatility contraction cycles and pivot breakout."""
        if len(df) < 30:
            return None

        closes = df["close"].values
        highs = df["high"].values
        lows = df["low"].values

        # Calculate high-low ranges over 3 contiguous windows
        w1_range = (np.max(highs[-30:-20]) - np.min(lows[-30:-20])) / np.mean(closes[-30:-20])
        w2_range = (np.max(highs[-20:-10]) - np.min(lows[-20:-10])) / np.mean(closes[-20:-10])
        w3_range = (np.max(highs[-10:]) - np.min(lows[-10:])) / np.mean(closes[-10:])

        latest_close = float(closes[-1])
        pivot_high = float(np.max(highs[-10:-1]))

        # Contraction condition: w1 > w2 > w3 (range decreasing) and latest_close > pivot_high
        if w1_range > w2_range > w3_range and latest_close > pivot_high:
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
                "confluence_score": 92.0,
                "reason": f"VCP Contraction Breakout ({w1_range*100:.1f}% -> {w2_range*100:.1f}% -> {w3_range*100:.1f}%)",
            }

        return None

