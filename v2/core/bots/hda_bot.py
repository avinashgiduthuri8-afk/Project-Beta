"""HDA Bot: High-Delivery Absorption Trading Archetype for Equities."""

from __future__ import annotations

from typing import Dict, Any, Optional
import pandas as pd



from v2.core.bots.base import BotArchetype
from v2.core.types import BotName


class HDABot(BotArchetype):
    """High-Delivery Absorption Archetype (Orderflow Accumulation)."""

    @property
    def name(self) -> BotName:
        return BotName.HDA

    def __init__(
        self,
        min_delivery_pct: float = 50.0,
        volume_surge_mult: float = 2.0,
        target_pct: float = 0.05,
        stop_pct: float = 0.02,
    ):

        self.min_delivery_pct = min_delivery_pct
        self.volume_surge_mult = volume_surge_mult
        self.target_pct = target_pct
        self.stop_pct = stop_pct

    def evaluate_setup(
        self,
        symbol: str,
        df: pd.DataFrame,
        delivery_pct: float = 55.0,
    ) -> Optional[Dict[str, Any]]:
        """Evaluates delivery volume percentage and volume surge absorption."""
        if len(df) < 20:
            return None

        volumes = df["volume"].values
        closes = df["close"].values

        latest_vol = float(volumes[-1])
        avg_vol = float(pd.Series(volumes[-20:]).mean())
        latest_close = float(closes[-1])

        # High Delivery (> 50%) and Volume Surge (> 2.0x 20-day average)
        if delivery_pct >= self.min_delivery_pct and latest_vol >= (avg_vol * self.volume_surge_mult):
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
                "confluence_score": 90.0,
                "reason": f"High Delivery Absorption ({delivery_pct:.1f}%) with {latest_vol/avg_vol:.1f}x Volume Surge",
            }

        return None

