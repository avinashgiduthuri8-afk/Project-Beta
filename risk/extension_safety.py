"""Price Extension & NSE Surveillance Safety Suite (Project-Beta)."""

from __future__ import annotations

import logging
from typing import Dict, Any, Tuple, Set

logger = logging.getLogger(__name__)

# SEBI Surveillance List (ASM / GSM)
SEBI_SURVEILLANCE_LIST: Set[str] = {
    "ADANIENT", "ADANIPOWER", "SUZLON", "IDEA", "YESBANK", "RCOM"
}


class ExtensionAndSafetyFilter:
    """
    Guards against:
    1. Overextended chasing (> 2.2x ATR distance from 20 EMA or > 4% from breakout pivot).
    2. Proximity to Upper/Lower Circuit limit (< 2.0% buffer).
    3. SEBI ASM/GSM surveillance list penalty.
    """

    def __init__(self, max_ema20_atr_dist: float = 2.2, max_chase_pct: float = 4.0):
        self.max_ema20_atr_dist = max_ema20_atr_dist
        self.max_chase_pct = max_chase_pct

    def evaluate_safety(
        self,
        symbol: str,
        price: float,
        ema_20: float,
        atr_14: float,
        pivot_price: Optional[float] = None,
        upper_circuit_price: Optional[float] = None,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        # 1. SEBI ASM/GSM Surveillance Check
        if symbol.upper() in SEBI_SURVEILLANCE_LIST:
            return False, f"Hard Gate: Symbol {symbol} is on SEBI ASM/GSM Surveillance List", {}

        # 2. Upper Circuit Proximity Filter (< 2% buffer)
        if upper_circuit_price and upper_circuit_price > 0:
            dist_to_circuit = ((upper_circuit_price - price) / upper_circuit_price) * 100.0
            if dist_to_circuit <= 2.0:
                return False, f"Hard Gate: Price ₹{price:.2f} within {dist_to_circuit:.1f}% of Upper Circuit ₹{upper_circuit_price:.2f}", {}

        # 3. Extension from 20 EMA in ATR units
        atr = max(atr_14, price * 0.01)
        dist_ema20_atr = (price - ema_20) / atr if atr > 0 else 0.0
        if dist_ema20_atr > self.max_ema20_atr_dist:
            return False, f"Overextended: Price is {dist_ema20_atr:.1f}x ATR above 20 EMA (Max allowed: {self.max_ema20_atr_dist}x)", {}

        # 4. Extension from Breakout Pivot
        if pivot_price and pivot_price > 0 and price > pivot_price:
            chase_pct = ((price - pivot_price) / pivot_price) * 100.0
            if chase_pct > self.max_chase_pct:
                return False, f"Chasing Filter: Price is +{chase_pct:.1f}% above breakout pivot (Max: {self.max_chase_pct}%)", {}

        return True, "Safety & Extension Checks Passed", {
            "dist_ema20_atr": round(dist_ema20_atr, 2),
            "is_safe": True,
        }
