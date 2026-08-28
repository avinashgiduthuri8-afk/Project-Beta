"""Market Regime & India VIX Volatility Detector (Project-Beta)."""

from __future__ import annotations

import logging
from typing import Dict, Any
from core.enums import MarketRegime

logger = logging.getLogger(__name__)


class MarketRegimeDetector:
    """Classifies broader Indian market regime using NIFTY 50, BANKNIFTY, and INDIA VIX."""

    @staticmethod
    def evaluate_regime(
        nifty_change_pct: float = 0.5,
        bank_change_pct: float = 0.4,
        vix_value: float = 14.5,
        vix_change_pct: float = -1.2,
    ) -> Dict[str, Any]:
        """Classify market regime into actionable states."""
        # 1. India VIX Assessment
        if vix_value < 12.0:
            vix_state = "LOW_VOLATILITY"
        elif vix_value <= 16.5:
            vix_state = "NORMAL"
        elif vix_value <= 21.0:
            vix_state = "ELEVATED"
        else:
            vix_state = "EXTREME_VOLATILITY"

        # 2. Regime Classification
        if vix_state == "EXTREME_VOLATILITY" or (vix_value > 20.0 and vix_change_pct > 10.0):
            regime = MarketRegime.HIGH_VOLATILITY_EXPANSION
            long_allowed = False
        elif nifty_change_pct > 0.4 and bank_change_pct > 0.3 and vix_value <= 17.0:
            regime = MarketRegime.BULLISH_TRENDING
            long_allowed = True
        elif nifty_change_pct < -0.5 and bank_change_pct < -0.5:
            regime = MarketRegime.BEARISH_TRENDING
            long_allowed = False
        elif abs(nifty_change_pct) <= 0.3 and vix_state in ("LOW_VOLATILITY", "NORMAL"):
            regime = MarketRegime.LOW_VOLATILITY_CHOP
            long_allowed = True
        else:
            regime = MarketRegime.NEUTRAL
            long_allowed = True

        return {
            "regime": regime,
            "vix_value": vix_value,
            "vix_state": vix_state,
            "nifty_change_pct": nifty_change_pct,
            "bank_change_pct": bank_change_pct,
            "long_setups_allowed": long_allowed,
            "confidence_multiplier": 1.2 if regime == MarketRegime.BULLISH_TRENDING else (0.7 if regime == MarketRegime.HIGH_VOLATILITY_EXPANSION else 1.0),
        }
