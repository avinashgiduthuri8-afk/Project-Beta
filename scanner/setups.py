"""High-Quality Technical Setup Detectors for Indian Stocks (Project-Beta)."""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple, Dict, Any
from core.models import Candle
from core.enums import SetupType
from scanner.indicators import Indicators

logger = logging.getLogger(__name__)


class SetupDetector:
    """Detects Minervini VCP, Pocket Pivot, NR7 Squeeze, and High-Delivery Breakouts."""

    @staticmethod
    def detect_minervini_vcp(candles: List[Candle]) -> Tuple[bool, Dict[str, Any]]:
        """
        Minervini Volatility Contraction Pattern (VCP):
        - Trend: Price > 50 EMA > 200 EMA (Stage 2 Uptrend).
        - Contractions: Depth of each pullback gets progressively tighter (e.g. 15% -> 8% -> 3%).
        - Volume: Volume contracts during consolidations and dries up before pivot breakout.
        """
        if len(candles) < 50:
            return False, {}

        ema_50 = Indicators.calculate_ema(candles, 50)
        ema_200 = Indicators.calculate_ema(candles, 200)
        current_close = candles[-1].close

        # Stage 2 Uptrend Hard Requirement
        if not (current_close > ema_50 and ema_50 >= ema_200):
            return False, {"reason": "Not in Stage 2 Uptrend (Close > 50 EMA > 200 EMA)"}

        # Measure recent 3 swing ranges (contractions)
        # Swing 1 (bars -30 to -15)
        high_1 = max(c.high for c in candles[-30:-15])
        low_1 = min(c.low for c in candles[-30:-15])
        depth_1 = ((high_1 - low_1) / high_1) * 100.0

        # Swing 2 (bars -15 to -5)
        high_2 = max(c.high for c in candles[-15:-5])
        low_2 = min(c.low for c in candles[-15:-5])
        depth_2 = ((high_2 - low_2) / high_2) * 100.0

        # Swing 3 (last 5 bars)
        high_3 = max(c.high for c in candles[-5:])
        low_3 = min(c.low for c in candles[-5:])
        depth_3 = ((high_3 - low_3) / high_3) * 100.0

        # VCP Geometry: Decreasing contraction depth
        is_contracting = (depth_1 > depth_2) and (depth_2 > depth_3) and (depth_3 <= 6.0)

        # Volume dry-up: last 3 bars average volume < 50-day average volume
        avg_vol_50 = sum(c.volume for c in candles[-50:]) / 50.0
        recent_vol = sum(c.volume for c in candles[-3:]) / 3.0
        volume_dry_up = recent_vol < (avg_vol_50 * 0.85)

        is_vcp = is_contracting or (depth_3 <= 5.0 and volume_dry_up)
        return is_vcp, {
            "setup": SetupType.MINERVINI_VCP,
            "depth_contractions": [round(depth_1, 1), round(depth_2, 1), round(depth_3, 1)],
            "pivot_price": round(high_3, 2),
            "volume_dry_up": volume_dry_up,
        }

    @staticmethod
    def detect_pocket_pivot(candles: List[Candle]) -> Tuple[bool, Dict[str, Any]]:
        """
        Pocket Pivot (Kacher/Morales):
        - Current bar is an up-day with volume greater than the highest down-day volume in last 10 bars.
        - Price is in an uptrend, consolidating near the 10 or 50 EMA.
        """
        if len(candles) < 15:
            return False, {}

        current_candle = candles[-1]
        is_up_day = current_candle.close > current_candle.open

        if not is_up_day:
            return False, {"reason": "Not an up-day"}

        # Find highest volume on down days in the prior 10 bars
        prior_down_volumes = [
            candles[i].volume
            for i in range(-11, -1)
            if candles[i].close < candles[i].open
        ]

        max_down_vol = max(prior_down_volumes) if prior_down_volumes else 0
        volume_pocket = current_candle.volume > max_down_vol

        ema_50 = Indicators.calculate_ema(candles, 50)
        near_ema = abs(current_candle.close - ema_50) / ema_50 <= 0.04  # within 4% of 50 EMA

        is_pp = volume_pocket and (current_candle.close >= ema_50 or near_ema)
        return is_pp, {
            "setup": SetupType.POCKET_PIVOT,
            "current_volume": current_candle.volume,
            "max_down_volume": max_down_vol,
            "volume_ratio": round(current_candle.volume / max(max_down_vol, 1), 2),
        }

    @staticmethod
    def detect_nr7_squeeze(candles: List[Candle]) -> Tuple[bool, Dict[str, Any]]:
        """
        NR7 Squeeze (Toby Crabel):
        - Today's range (High - Low) is the narrowest among the last 7 trading days.
        - Signals imminent volatility explosion / directional breakout.
        """
        if len(candles) < 7:
            return False, {}

        ranges = [c.high - c.low for c in candles[-7:]]
        current_range = ranges[-1]
        is_nr7 = current_range == min(ranges)

        return is_nr7, {
            "setup": SetupType.NR7_SQUEEZE,
            "current_range": round(current_range, 2),
            "prior_ranges": [round(r, 2) for r in ranges[:-1]],
        }

    @staticmethod
    def detect_high_delivery_breakout(candles: List[Candle], delivery_pct: float, avg_delivery_pct: float) -> Tuple[bool, Dict[str, Any]]:
        """
        High Delivery Breakout:
        - Breakout candle accompanied by institutional delivery percentage > 50%
        - Delivery volume is at least 1.3x higher than the 20-day average delivery.
        """
        if len(candles) < 20:
            return False, {}

        current_candle = candles[-1]
        highest_20_close = max(c.close for c in candles[-21:-1])
        is_breakout = current_candle.close > highest_20_close

        delivery_surge = (delivery_pct >= 50.0) or (delivery_pct > (avg_delivery_pct * 1.3))

        return (is_breakout and delivery_surge), {
            "setup": SetupType.HIGH_DELIVERY_BREAKOUT,
            "delivery_percentage": round(delivery_pct, 1),
            "avg_delivery_percentage": round(avg_delivery_pct, 1),
            "breakout_level": round(highest_20_close, 2),
        }
