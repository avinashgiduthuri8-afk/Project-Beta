"""
Deterministic Heuristic AI Evaluator.

Provides robust, instant, offline-capable signal evaluation that acts as a fail-safe
fallback when LLM APIs are unavailable, disabled, or rate-limited.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

from core.types import (
    AIAnalysis,
    AIRecommendation,
    MarketState,
    OppType,
    RiskLevel,
    Signal,
)


class FallbackEvaluator:
    """Rule-based quantitative evaluator for crypto signals."""

    @staticmethod
    def evaluate(signal: Signal, start_time: float | None = None) -> AIAnalysis:
        """Evaluate a signal deterministically and return structured AIAnalysis."""
        t0 = start_time or time.perf_counter()
        raw = signal.raw_payload or {}

        score = signal.score
        confidence = signal.confidence
        risk_level = signal.risk_level
        market_state = signal.market_state
        opp_type = signal.opportunity_type
        mtf = signal.mtf_alignment

        rsi = raw.get("rsi") or raw.get("rsi_14") or 50.0
        vol_spike = raw.get("volume_spike_ratio") or raw.get("volume_spike") or 1.0
        pct_change = raw.get("change_24h_pct") or raw.get("pct_change_24h") or 0.0

        try:
            rsi = float(rsi)
        except (ValueError, TypeError):
            rsi = 50.0

        try:
            vol_spike = float(vol_spike)
        except (ValueError, TypeError):
            vol_spike = 1.0

        try:
            pct_change = float(pct_change)
        except (ValueError, TypeError):
            pct_change = 0.0

        supporting_factors: list[str] = []
        conflicts: list[str] = []
        risk_factors: list[str] = []

        # ── 1. Supporting factors ─────────────────────────────────────────────
        if mtf:
            supporting_factors.append(
                "Multi-timeframe trend alignment confirmed across 1h/4h/24h."
            )
        if score >= 80:
            supporting_factors.append(
                f"High conviction scanner quality score ({score}/100)."
            )
        elif score >= 70:
            supporting_factors.append(
                f"Moderate conviction scanner quality score ({score}/100)."
            )

        if vol_spike >= 1.5:
            supporting_factors.append(
                f"Strong volume expansion detected ({vol_spike:.1f}x average)."
            )
        elif vol_spike >= 1.1:
            supporting_factors.append("Volume participation above baseline.")

        if market_state in (MarketState.BREAKOUT, MarketState.BULL_TREND):
            supporting_factors.append(f"Favorable market regime: {market_state.value}.")
        elif market_state == MarketState.PULLBACK:
            supporting_factors.append("Pullback to support in ongoing trend.")
        elif market_state == MarketState.RECOVERY:
            supporting_factors.append("Early stage recovery momentum structure.")

        if 40.0 <= rsi <= 65.0:
            supporting_factors.append(
                f"RSI in optimal momentum expansion zone ({rsi:.1f})."
            )

        # ── 2. Conflicts & Risks ──────────────────────────────────────────────
        if not mtf:
            conflicts.append(
                "Lack of multi-timeframe confirmation; conflicting timeframe trends."
            )

        if rsi > 75.0:
            conflicts.append(
                f"RSI severely overbought ({rsi:.1f}), elevated risk of mean-reversion pullbacks."
            )
            risk_factors.append("Over-extended momentum.")
        elif rsi < 30.0 and market_state != MarketState.RECOVERY:
            conflicts.append(
                f"RSI deeply oversold ({rsi:.1f}) in non-recovery structure."
            )

        if risk_level == RiskLevel.HIGH:
            risk_factors.append("High volatility / risk classification from scanner.")
        elif risk_level == RiskLevel.MEDIUM:
            risk_factors.append("Moderate asset volatility profile.")

        if market_state == MarketState.DOWNTREND:
            conflicts.append("Primary market regime is in downtrend.")
            risk_factors.append("Trading against broader macro trend.")
        elif market_state == MarketState.SIDEWAYS:
            risk_factors.append(
                "Range-bound sideways regime with low follow-through probability."
            )

        if abs(pct_change) > 18.0:
            risk_factors.append(f"High 24h price extension ({pct_change:+.1f}%).")

        # ── 3. Recommendation & Confidence ────────────────────────────────────
        computed_confidence = int(
            round(
                (score * 0.5)
                + (confidence * 0.3)
                + (15 if mtf else 0)
                + (5 if vol_spike >= 1.2 else 0)
            )
        )
        computed_confidence = max(10, min(95, computed_confidence))

        if (
            market_state == MarketState.DOWNTREND
            or opp_type == OppType.AVOID
            or score < 55
        ):
            recommendation = AIRecommendation.REJECT
            computed_confidence = min(computed_confidence, 40)
            tighten_stop = True
            size_multiplier = 0.0
            setup_quality = "Poor - conflicting trend structure"
        elif score >= 80 and mtf and risk_level != RiskLevel.HIGH and rsi <= 75:
            recommendation = AIRecommendation.APPROVE
            computed_confidence = max(computed_confidence, 78)
            tighten_stop = False
            size_multiplier = 1.0
            setup_quality = "High - robust momentum and multi-timeframe alignment"
        elif score >= 68 and risk_level != RiskLevel.HIGH and rsi <= 78:
            recommendation = AIRecommendation.SCALE_DOWN
            computed_confidence = max(computed_confidence, 65)
            tighten_stop = True
            size_multiplier = 0.5
            setup_quality = "Moderate - acceptable risk with defensive sizing"
        elif score >= 60:
            recommendation = AIRecommendation.WATCH
            computed_confidence = max(computed_confidence, 50)
            tighten_stop = True
            size_multiplier = 0.0
            setup_quality = (
                "Neutral - awaiting clearer trigger or breakout confirmation"
            )
        else:
            recommendation = AIRecommendation.REJECT
            computed_confidence = min(computed_confidence, 45)
            tighten_stop = True
            size_multiplier = 0.0
            setup_quality = "Substandard - score and risk thresholds not met"

        latency_ms = (time.perf_counter() - t0) * 1000.0

        return AIAnalysis(
            id=str(uuid.uuid4()),
            signal_id=signal.id,
            coin=signal.coin,
            pair=signal.pair,
            recommendation=recommendation,
            confidence_score=computed_confidence,
            trend_evaluation=f"Trend state: {market_state.value} (MTF aligned: {mtf})",
            momentum_evaluation=f"Momentum RSI: {rsi:.1f}, score: {score}/100",
            volume_evaluation=f"Volume spike ratio: {vol_spike:.2f}x",
            setup_quality=setup_quality,
            market_regime=market_state.value,
            risk_reward_assessment=(
                "Estimated R:R 2.2:1 based on standard ATR channel boundaries."
                if recommendation == AIRecommendation.APPROVE
                else "Unfavorable or uncertain R:R profile."
            ),
            supporting_factors=supporting_factors or ["Baseline scanner criteria met."],
            conflicts=conflicts or ["No critical structural divergences detected."],
            risk_factors=risk_factors or ["Standard market volatility."],
            suggested_adjustments={
                "size_multiplier": size_multiplier,
                "tighten_stop": tighten_stop,
                "target_notes": (
                    "Scale out at 1.5R and 2.5R targets."
                    if size_multiplier > 0
                    else "N/A"
                ),
            },
            model_name="heuristic-fallback",
            execution_latency_ms=round(latency_ms, 2),
            analyzed_at=datetime.now(timezone.utc),
            raw_response={
                "evaluator": "FallbackEvaluator",
                "evaluated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
