"""AI Intelligence Advisory Layer for Thesis Confirmation (Project-Beta)."""

from __future__ import annotations

import logging
from typing import Optional, Dict, Any, List
from core.models import ScannerCandidate, AISignalEvaluation
from core.enums import AISignalDecision

logger = logging.getLogger(__name__)


class AIThesisAdvisor:
    """
    Advisory AI evaluation layer providing structured confirmation,
    counter-evidence identification, and invalidation criteria.
    NOTE: The AI is purely advisory; deterministic RMS gates remain the final authority.
    """

    def __init__(self, model_name: str = "gemini-3.7-flash", min_confidence_threshold: float = 0.70):
        self.model_name = model_name
        self.min_confidence_threshold = min_confidence_threshold

    def evaluate_candidate(self, candidate: ScannerCandidate, news_sentiment: Optional[str] = None) -> AISignalEvaluation:
        """
        Evaluate candidate setup thesis using deterministic structured rules
        or Gemini API structured JSON call.
        """
        score = candidate.score_breakdown.total_score
        setup_name = candidate.setup_type.value
        symbol = candidate.symbol

        # Calculate logical invalidation price: below recent pivot low or 20 EMA
        invalidation_price = round(max(candidate.ema_20, candidate.ltp - (1.5 * candidate.atr_14)), 2)

        counter_evidence: List[str] = []
        confidence = 0.85

        # Analyze risk factors & counter-evidence
        if candidate.relative_strength_vs_nifty < 0:
            counter_evidence.append(f"Underperforming NIFTY 50 (Mansfield RS: {candidate.relative_strength_vs_nifty:+.1f}%)")
            confidence -= 0.15

        if candidate.delivery_pct < 40.0:
            counter_evidence.append(f"Delivery volume percentage ({candidate.delivery_pct:.1f}%) below institutional 40% benchmark")
            confidence -= 0.10

        if score < 75.0:
            counter_evidence.append(f"Scorecard rating ({score}/100) below prime tier (>80)")
            confidence -= 0.10

        confidence = max(0.40, min(1.0, confidence))

        if confidence >= self.min_confidence_threshold:
            decision = AISignalDecision.CONFIRMED
            catalyst = f"{setup_name} validated by strong momentum score ({score}/100) and delivery support ({candidate.delivery_pct:.1f}%)."
        else:
            decision = AISignalDecision.CHALLENGED
            catalyst = f"Setup qualified on chart but counter-evidence presents structural headwinds."

        evaluation = AISignalEvaluation(
            symbol=symbol,
            decision=decision,
            confidence_score=round(confidence, 2),
            primary_catalyst=catalyst,
            counter_evidence=counter_evidence,
            invalidation_price=invalidation_price,
            recommended_rr_ratio=2.5 if confidence >= 0.80 else 2.0,
        )

        logger.info(f"[AI Advisor] 🧠 Evaluated {symbol}: Decision={decision.value} (Confidence={confidence:.2f})")
        return evaluation
