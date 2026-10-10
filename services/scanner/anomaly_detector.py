"""
PROJECT-ALPHA — Anomaly Detection Engine (Skill 20).

Detects abnormal price, volume, and spread behavior on CoinDCX pairs:
  1. Volume Outliers (Z-Score): Detects sudden volume spikes (> 3.5 sigma above rolling mean).
  2. Wick / Shadow Anomalies: Detects abnormal wick-to-range ratios (> 75% wick, indicating stop-hunts / flash sweeps).
  3. Range / Spread Blowout: Detects candle true ranges expanding beyond 4x recent ATR.
  4. Pump & Dump Momentum Surges: Flags parabolic 24h / intraday momentum surges.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from core.logging import get_logger

logger = get_logger("scanner.anomaly_detector")


class AnomalyType(str, Enum):
    VOLUME_ZSCORE_SPIKE = "VOLUME_ZSCORE_SPIKE"
    EXTREME_UPPER_WICK = "EXTREME_UPPER_WICK"
    EXTREME_LOWER_WICK = "EXTREME_LOWER_WICK"
    RANGE_EXPANSION_BLOWOUT = "RANGE_EXPANSION_BLOWOUT"
    PARABOLIC_PUMP = "PARABOLIC_PUMP"


@dataclass
class AnomalyReport:
    """Detailed anomaly evaluation for a coin / candle sequence."""
    is_anomalous: bool
    anomalies_detected: list[AnomalyType] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    risk_penalty: float = 0.0  # 0.0 to 1.0 confidence penalty
    rejection_reason: str | None = None


class AnomalyDetector:
    """
    Statistical Anomaly Detector evaluating volume z-scores, wick ratios, and spread expansions.
    """

    def __init__(
        self,
        volume_zscore_threshold: float = 3.5,
        max_wick_ratio: float = 0.75,
        max_range_atr_multiplier: float = 4.0,
        max_momentum_pct: float = 25.0,
    ) -> None:
        self.volume_zscore_threshold = volume_zscore_threshold
        self.max_wick_ratio = max_wick_ratio
        self.max_range_atr_multiplier = max_range_atr_multiplier
        self.max_momentum_pct = max_momentum_pct

    def calculate_volume_zscore(self, volumes: list[float]) -> tuple[bool, float]:
        """
        Calculate z-score of the most recent volume against rolling historical baseline.
        Returns: (is_spike, z_score)
        """
        if not volumes or len(volumes) < 5:
            return False, 0.0

        history = volumes[:-1]
        target = volumes[-1]

        mean_vol = sum(history) / len(history)
        variance = sum((x - mean_vol) ** 2 for x in history) / len(history)
        std_vol = math.sqrt(variance)

        if std_vol < 1e-6:
            if mean_vol > 0 and target >= mean_vol * 3.0:
                # Flat historical volume baseline that suddenly jumped
                pseudo_z = round((target - mean_vol) / mean_vol, 2)
                return True, pseudo_z
            return False, 0.0

        z_score = (target - mean_vol) / std_vol
        is_spike = z_score >= self.volume_zscore_threshold
        return is_spike, round(z_score, 2)

    def evaluate_wick_anomaly(self, candle: dict[str, Any]) -> tuple[bool, AnomalyType | None, float]:
        """
        Detects extreme upper or lower shadow indicating aggressive liquidity sweeps / stop hunts.
        Returns: (is_anomaly, anomaly_type, max_wick_ratio)
        """
        try:
            o = float(candle.get("open", candle.get("o", 0.0)) or 0.0)
            h = float(candle.get("high", candle.get("h", 0.0)) or 0.0)
            l = float(candle.get("low", candle.get("l", 0.0)) or 0.0)
            c = float(candle.get("close", candle.get("c", 0.0)) or 0.0)

            total_range = h - l
            if total_range <= 1e-6:
                return False, None, 0.0

            body_top = max(o, c)
            body_bottom = min(o, c)

            upper_wick = h - body_top
            lower_wick = body_bottom - l

            upper_ratio = upper_wick / total_range
            lower_ratio = lower_wick / total_range

            if upper_ratio >= self.max_wick_ratio:
                return True, AnomalyType.EXTREME_UPPER_WICK, round(upper_ratio, 3)
            elif lower_ratio >= self.max_wick_ratio:
                return True, AnomalyType.EXTREME_LOWER_WICK, round(lower_ratio, 3)

            return False, None, round(max(upper_ratio, lower_ratio), 3)
        except (ValueError, TypeError):
            return False, None, 0.0

    def evaluate_range_blowout(
        self, candle: dict[str, Any], recent_ranges: list[float]
    ) -> tuple[bool, float]:
        """
        Detects anomalous expansion in true range compared to rolling average range.
        Returns: (is_blowout, multiplier)
        """
        if not recent_ranges or len(recent_ranges) < 5:
            return False, 1.0

        try:
            h = float(candle.get("high", candle.get("h", 0.0)) or 0.0)
            l = float(candle.get("low", candle.get("l", 0.0)) or 0.0)
            curr_range = h - l

            baseline_range = sum(recent_ranges) / len(recent_ranges)
            if baseline_range <= 1e-6:
                return False, 1.0

            multiplier = curr_range / baseline_range
            is_blowout = multiplier >= self.max_range_atr_multiplier
            return is_blowout, round(multiplier, 2)
        except (ValueError, TypeError):
            return False, 1.0

    def evaluate_series(self, candles: list[dict[str, Any]]) -> AnomalyReport:
        """
        Full anomaly diagnostic on a series of historical/live candles.
        """
        if not candles or len(candles) < 5:
            return AnomalyReport(is_anomalous=False)

        anomalies: list[AnomalyType] = []
        metrics: dict[str, float] = {}
        risk_penalty = 0.0
        rejection_reasons: list[str] = []

        volumes = [
            float(c.get("volume", c.get("v", 0.0)) or 0.0) for c in candles
        ]
        is_vol_spike, z_score = self.calculate_volume_zscore(volumes)
        metrics["volume_zscore"] = z_score

        if is_vol_spike:
            anomalies.append(AnomalyType.VOLUME_ZSCORE_SPIKE)
            risk_penalty += 0.25
            logger.warning("Volume anomaly detected: z-score %.2f >= threshold %.2f", z_score, self.volume_zscore_threshold)

        # 2. Wick Anomaly on latest candle
        latest_candle = candles[-1]
        is_wick, wick_type, wick_ratio = self.evaluate_wick_anomaly(latest_candle)
        metrics["wick_ratio"] = wick_ratio

        if is_wick and wick_type:
            anomalies.append(wick_type)
            risk_penalty += 0.35
            rejection_reasons.append(f"Extreme wick ratio {wick_ratio:.1%} ({wick_type.value})")

        # 3. Range Blowout on latest candle
        historical_ranges = [
            float(c.get("high", c.get("h", 0.0)) or 0.0) - float(c.get("low", c.get("l", 0.0)) or 0.0)
            for c in candles[:-1]
        ]
        is_blowout, range_mult = self.evaluate_range_blowout(latest_candle, historical_ranges)
        metrics["range_multiplier"] = range_mult

        if is_blowout:
            anomalies.append(AnomalyType.RANGE_EXPANSION_BLOWOUT)
            risk_penalty += 0.40
            rejection_reasons.append(f"Range blowout {range_mult:.1f}x baseline")

        # 4. Parabolic Momentum (Open of first candle to Close of latest)
        first_c = float(candles[0].get("close", candles[0].get("c", 0.0)) or 0.0)
        last_c = float(latest_candle.get("close", latest_candle.get("c", 0.0)) or 0.0)
        if first_c > 0:
            price_change_pct = abs((last_c - first_c) / first_c) * 100.0
            metrics["series_price_change_pct"] = round(price_change_pct, 2)
            if price_change_pct > self.max_momentum_pct:
                anomalies.append(AnomalyType.PARABOLIC_PUMP)
                risk_penalty += 0.50
                rejection_reasons.append(f"Parabolic price surge {price_change_pct:.1f}% > {self.max_momentum_pct}%")

        is_anomalous = len(anomalies) > 0
        total_penalty = min(1.0, risk_penalty)
        reason_msg = "; ".join(rejection_reasons) if rejection_reasons else None

        return AnomalyReport(
            is_anomalous=is_anomalous,
            anomalies_detected=anomalies,
            metrics=metrics,
            risk_penalty=round(total_penalty, 2),
            rejection_reason=reason_msg,
        )
