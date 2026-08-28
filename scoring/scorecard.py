"""Auditable 100-Point Signal Scoring Framework with Deterministic Hard Gates."""

from __future__ import annotations

import logging
from typing import List, Dict, Any, Tuple
from core.models import Candle, ScoreBreakdown
from core.enums import SetupType
from scanner.indicators import Indicators

logger = logging.getLogger(__name__)


class SignalScorecard:
    """Evaluates candidate quality on an auditable 100-point scale across 4 distinct dimensions."""

    @classmethod
    def evaluate(
        cls,
        daily_candles: List[Candle],
        h1_candles: List[Candle],
        m15_candles: List[Candle],
        setup_type: SetupType,
        setup_metadata: Dict[str, Any],
        mansfield_rs: float,
        delivery_pct: float,
        avg_delivery_pct: float,
    ) -> ScoreBreakdown:
        if len(daily_candles) < 50:
            return ScoreBreakdown(
                total_score=0.0,
                hard_gates_passed=False,
                rejection_reason="Insufficient daily candle history (< 50 bars)",
            )

        current_price = daily_candles[-1].close
        ema_20 = Indicators.calculate_ema(daily_candles, 20)
        ema_50 = Indicators.calculate_ema(daily_candles, 50)
        ema_200 = Indicators.calculate_ema(daily_candles, 200)

        # -------------------------------------------------------------
        # 1. HARD ELIMINATION GATES (Non-negotiable)
        # -------------------------------------------------------------
        # Long candidates below 200 EMA are automatically disqualified
        if current_price < ema_200:
            return ScoreBreakdown(
                total_score=0.0,
                hard_gates_passed=False,
                rejection_reason=f"Hard Gate Breach: Price ₹{current_price:.2f} is below 200 EMA ₹{ema_200:.2f}",
            )

        # -------------------------------------------------------------
        # 2. DIMENSION 1: Trend & Market Regime (Max 25 pts)
        # -------------------------------------------------------------
        trend_score = 0.0
        # Alignment of EMAs: 20 > 50 > 200
        if current_price > ema_20 > ema_50 > ema_200:
            trend_score += 15.0
        elif current_price > ema_50 > ema_200:
            trend_score += 10.0
        elif current_price > ema_200:
            trend_score += 5.0

        # Mansfield RS vs NIFTY 50 outperformance
        if mansfield_rs > 5.0:
            trend_score += 10.0
        elif mansfield_rs > 0.0:
            trend_score += 6.0
        trend_score = min(trend_score, 25.0)

        # -------------------------------------------------------------
        # 3. DIMENSION 2: Setup Geometry & Pattern Cleanliness (Max 25 pts)
        # -------------------------------------------------------------
        geometry_score = 0.0
        if setup_type == SetupType.MINERVINI_VCP:
            geometry_score = 22.0 if setup_metadata.get("volume_dry_up") else 18.0
        elif setup_type == SetupType.POCKET_PIVOT:
            geometry_score = 20.0
        elif setup_type == SetupType.NR7_SQUEEZE:
            geometry_score = 18.0
        elif setup_type == SetupType.HIGH_DELIVERY_BREAKOUT:
            geometry_score = 21.0
        else:
            geometry_score = 14.0
        geometry_score = min(geometry_score, 25.0)

        # -------------------------------------------------------------
        # 4. DIMENSION 3: Institutional Volume & Delivery Footprint (Max 25 pts)
        # -------------------------------------------------------------
        volume_score = 0.0
        avg_vol_20 = sum(c.volume for c in daily_candles[-20:]) / 20.0 if len(daily_candles) >= 20 else 1.0
        vol_surge_ratio = daily_candles[-1].volume / max(avg_vol_20, 1.0)

        if vol_surge_ratio >= 2.0:
            volume_score += 12.0
        elif vol_surge_ratio >= 1.3:
            volume_score += 8.0
        else:
            volume_score += 4.0

        if delivery_pct >= 55.0:
            volume_score += 13.0
        elif delivery_pct >= 40.0:
            volume_score += 8.0
        elif delivery_pct > avg_delivery_pct:
            volume_score += 5.0
        volume_score = min(volume_score, 25.0)

        # -------------------------------------------------------------
        # 5. DIMENSION 4: Multi-Timeframe Confirmation (Max 25 pts)
        # -------------------------------------------------------------
        mtf_score = 0.0
        # H1 timeframe alignment
        if h1_candles and len(h1_candles) >= 20:
            h1_ema20 = Indicators.calculate_ema(h1_candles, 20)
            if h1_candles[-1].close > h1_ema20:
                mtf_score += 12.0

        # M15 trigger: Price > VWAP and RSI between 55-70 (Bullish Momentum zone)
        if m15_candles and len(m15_candles) >= 14:
            m15_last = m15_candles[-1]
            m15_rsi = Indicators.calculate_rsi(m15_candles, 14)
            if m15_last.vwap and m15_last.close > m15_last.vwap:
                mtf_score += 8.0
            if 55.0 <= m15_rsi <= 72.0:
                mtf_score += 5.0
            elif m15_rsi > 50.0:
                mtf_score += 3.0
        else:
            mtf_score += 8.0  # Baseline fallback
        mtf_score = min(mtf_score, 25.0)

        total = round(trend_score + geometry_score + volume_score + mtf_score, 1)

        return ScoreBreakdown(
            trend_regime_score=round(trend_score, 1),
            setup_geometry_score=round(geometry_score, 1),
            volume_delivery_score=round(volume_score, 1),
            mtb_confirmation_score=round(mtf_score, 1),
            total_score=total,
            hard_gates_passed=True,
            rejection_reason=None,
        )
