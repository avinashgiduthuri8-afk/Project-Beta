"""C2 Confluence Scoring Engine (4-Pillar Weighted Scorecard)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class ConfluenceWeights:
    chart_structure: float = 0.40   # 40% Weight
    indicator_align: float = 0.25   # 25% Weight
    market_regime: float = 0.20     # 20% Weight
    sentiment_news: float = 0.15    # 15% Weight
    min_elite_threshold: float = 85.0


class C2ConfluenceEngine:
    """Computes weighted multi-pillar confluence scorecard for candidate stock setups."""

    def __init__(self, weights: Optional[ConfluenceWeights] = None):
        self.weights = weights or ConfluenceWeights()

    def calculate_score(
        self,
        chart_score: float,       # 0.0 to 100.0
        indicator_score: float,   # 0.0 to 100.0
        regime_score: float,      # 0.0 to 100.0
        sentiment_score: float,   # 0.0 to 100.0
    ) -> Dict[str, Any]:
        """Calculates total weighted score and determines ELITE pass status.

        Confluence Score = (W_chart * S_chart) + (W_ind * S_ind) + (W_regime * S_regime) + (W_sent * S_sent)
        """
        c_score = max(0.0, min(100.0, chart_score))
        i_score = max(0.0, min(100.0, indicator_score))
        r_score = max(0.0, min(100.0, regime_score))
        s_score = max(0.0, min(100.0, sentiment_score))

        w = self.weights
        total_score = (
            (w.chart_structure * c_score)
            + (w.indicator_align * i_score)
            + (w.market_regime * r_score)
            + (w.sentiment_news * s_score)
        )

        is_elite = total_score >= w.min_elite_threshold

        return {
            "total_score": round(total_score, 2),
            "is_elite": is_elite,
            "threshold": w.min_elite_threshold,
            "pillar_breakdown": {
                "chart_structure": round(c_score * w.chart_structure, 2),
                "indicator_align": round(i_score * w.indicator_align, 2),
                "market_regime": round(r_score * w.market_regime, 2),
                "sentiment_news": round(s_score * w.sentiment_news, 2),
            },
        }

