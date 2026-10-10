"""
PROJECT-ALPHA — Data-Vendor Cross-Validation Engine for Backtesting (Skill 18).

Cross-validates historical candle datasets across secondary data sources to filter bad ticks and feed errors:
  1. Multi-Feed Timestamp Alignment (Aligns primary CoinDCX bars with secondary exchanges)
  2. Price Variance & Spread Divergence Auditing (Flags deviations > 0.5%)
  3. Bad-Tick & Outlier Filtering
  4. Feed Reliability Score (0.0 - 100.0%)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.logging import get_logger

logger = get_logger("background.backtest.vendor_cross_val")


@dataclass
class CrossValidationReport:
    """Historical data vendor cross-validation audit report."""

    pair: str
    timeframe: str
    is_valid: bool
    reliability_score: float  # 0.0 - 100.0%
    total_bars_compared: int
    bad_ticks_count: int
    price_variance_breaches: int
    timestamp_discrepancies: int
    anomalies: list[dict[str, Any]] = field(default_factory=list)
    summary_message: str = ""


class VendorCrossValidator:
    """
    Audits and cross-validates historical price feeds for backtesting integrity.
    """

    def __init__(
        self,
        max_price_variance_pct: float = 0.50,  # 0.50% max allowed price deviation between feeds
        min_reliability_pct: float = 95.0,  # 95.0% minimum dataset reliability
    ) -> None:
        self.max_price_variance_pct = max_price_variance_pct
        self.min_reliability_pct = min_reliability_pct

    def cross_validate_series(
        self,
        primary_candles: list[dict[str, Any]],
        secondary_candles: list[dict[str, Any]],
        pair: str = "",
        timeframe: str = "1h",
    ) -> CrossValidationReport:
        """
        Cross-validates primary candle sequence against a secondary feed benchmark.
        """
        if not primary_candles or not secondary_candles:
            return CrossValidationReport(
                pair=pair,
                timeframe=timeframe,
                is_valid=False,
                reliability_score=0.0,
                total_bars_compared=0,
                bad_ticks_count=0,
                price_variance_breaches=0,
                timestamp_discrepancies=0,
                summary_message="Insufficient candle datasets for cross-validation",
            )

        # Index secondary feed by timestamp (ms)
        sec_map: dict[int, dict[str, Any]] = {}
        for c in secondary_candles:
            ts = int(c.get("timestamp", c.get("time", c.get("t", 0))) or 0)
            if ts > 0:
                sec_map[ts] = c

        compared_count = 0
        variance_breaches = 0
        bad_ticks = 0
        timestamp_gaps = 0
        anomalies: list[dict[str, Any]] = []

        for p_cand in primary_candles:
            p_ts = int(p_cand.get("timestamp", p_cand.get("time", p_cand.get("t", 0))) or 0)
            if p_ts <= 0:
                continue

            if p_ts not in sec_map:
                timestamp_gaps += 1
                anomalies.append({
                    "timestamp": p_ts,
                    "reason": "MISSING_SECONDARY_TIMESTAMP",
                })
                continue

            compared_count += 1
            s_cand = sec_map[p_ts]

            p_close = float(p_cand.get("close", p_cand.get("c", 0.0)) or 0.0)
            s_close = float(s_cand.get("close", s_cand.get("c", 0.0)) or 0.0)

            if p_close <= 0 or s_close <= 0:
                bad_ticks += 1
                anomalies.append({
                    "timestamp": p_ts,
                    "reason": "NON_POSITIVE_PRICE",
                    "primary_close": p_close,
                    "secondary_close": s_close,
                })
                continue

            # Price variance calculation
            diff_pct = (abs(p_close - s_close) / s_close) * 100.0
            if diff_pct > self.max_price_variance_pct:
                variance_breaches += 1
                anomalies.append({
                    "timestamp": p_ts,
                    "reason": "EXCESSIVE_PRICE_VARIANCE",
                    "primary_close": p_close,
                    "secondary_close": s_close,
                    "variance_pct": round(diff_pct, 2),
                })

        if compared_count == 0:
            return CrossValidationReport(
                pair=pair,
                timeframe=timeframe,
                is_valid=False,
                reliability_score=0.0,
                total_bars_compared=0,
                bad_ticks_count=bad_ticks,
                price_variance_breaches=variance_breaches,
                timestamp_discrepancies=timestamp_gaps,
                anomalies=anomalies,
                summary_message="No matching timestamps found between primary and secondary feeds",
            )

        fault_count = variance_breaches + bad_ticks
        reliability_score = max(
            0.0, min(100.0, ((compared_count - fault_count) / compared_count) * 100.0)
        )
        is_valid = reliability_score >= self.min_reliability_pct

        summary = (
            f"Cross-validated {compared_count} bars for {pair} ({timeframe}): "
            f"Reliability {reliability_score:.1f}%, {variance_breaches} variance breaches, "
            f"{bad_ticks} bad ticks, {timestamp_gaps} timestamp gaps."
        )

        return CrossValidationReport(
            pair=pair,
            timeframe=timeframe,
            is_valid=is_valid,
            reliability_score=round(reliability_score, 1),
            total_bars_compared=compared_count,
            bad_ticks_count=bad_ticks,
            price_variance_breaches=variance_breaches,
            timestamp_discrepancies=timestamp_gaps,
            anomalies=anomalies,
            summary_message=summary,
        )

