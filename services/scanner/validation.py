"""
PROJECT-ALPHA — Market Data Validation Engine (Skill 04).

Detects corrupted, missing, inverted, or anomalous OHLCV market data from CoinDCX:
  1. Price Bounds: Strict positive values (open, high, low, close > 0).
  2. Structural Geometry: High >= max(Open, Close), Low <= min(Open, Close), High >= Low.
  3. Volume Bounds: Volume >= 0.
  4. Sequence & Monotonicity: Chronological ordering and duplicate timestamp resolution.
  5. Timeframe Gap Detection: Identifies missing intervals for standard bar durations (15m, 1h, 1d).
  6. Flash Jump Sanity: Flags extreme single-bar price percentage anomalies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from core.logging import get_logger

logger = get_logger("scanner.validation")


class ValidationStatus(str, Enum):
    VALID = "VALID"
    INVALID_PRICE = "INVALID_PRICE"
    INVERTED_OHLC = "INVERTED_OHLC"
    NEGATIVE_VOLUME = "NEGATIVE_VOLUME"
    INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
    NON_MONOTONIC_TIMESTAMP = "NON_MONOTONIC_TIMESTAMP"
    EXCESSIVE_JUMP = "EXCESSIVE_JUMP"


# Expected duration in milliseconds for common timeframes
TIMEFRAME_INTERVAL_MS: dict[str, int] = {
    "1m": 60 * 1000,
    "5m": 5 * 60 * 1000,
    "15m": 15 * 60 * 1000,
    "30m": 30 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "2h": 2 * 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
    "1d": 24 * 60 * 60 * 1000,
}


@dataclass
class CandleValidationResult:
    """Structured report returned after validating a candle series."""
    is_valid: bool
    cleaned_candles: list[dict[str, Any]] = field(default_factory=list)
    corrupted_count: int = 0
    anomalies: list[dict[str, Any]] = field(default_factory=list)
    gaps: list[dict[str, Any]] = field(default_factory=list)
    summary_message: str = ""


class MarketDataValidator:
    """
    Market Data Validator for CoinDCX candle feeds and backtesting data.
    Enforces financial geometry invariants and flags feed anomalies.
    """

    def __init__(
        self,
        max_jump_ratio: float = 3.0,  # 300% single-bar jump threshold
        allow_geometry_repair: bool = False,
    ) -> None:
        self.max_jump_ratio = max_jump_ratio
        self.allow_geometry_repair = allow_geometry_repair

    def validate_candle(
        self, candle: dict[str, Any]
    ) -> tuple[bool, ValidationStatus, dict[str, Any]]:
        """
        Validate single candle dictionary.
        Returns: (is_valid, status, normalized_candle)
        """
        try:
            raw_ts = candle.get("timestamp", candle.get("time", candle.get("t", 0)))
            ts_ms = int(raw_ts or 0)
            # If timestamp is in seconds (< 1e11), convert to milliseconds
            if 0 < ts_ms < 100_000_000_000:
                ts_ms *= 1000

            if ts_ms <= 0:
                return False, ValidationStatus.INVALID_TIMESTAMP, candle

            o = float(candle.get("open", candle.get("o", 0.0)) or 0.0)
            h = float(candle.get("high", candle.get("h", 0.0)) or 0.0)
            l = float(candle.get("low", candle.get("l", 0.0)) or 0.0)
            c = float(candle.get("close", candle.get("c", 0.0)) or 0.0)
            v = float(candle.get("volume", candle.get("v", 0.0)) or 0.0)

            # 1. Price Bounds Check
            if o <= 0.0 or h <= 0.0 or l <= 0.0 or c <= 0.0:
                return False, ValidationStatus.INVALID_PRICE, candle

            # 2. Volume Bounds Check
            if v < 0.0:
                return False, ValidationStatus.NEGATIVE_VOLUME, candle

            # 3. Structural Geometry Invariants
            # High must be >= max(open, close), Low must be <= min(open, close), High >= Low
            expected_h = max(h, o, c)
            expected_l = min(l, o, c)

            # Check if geometry is structurally inverted beyond small floating point margin
            eps = 1e-6
            is_inverted = (h < (max(o, c) - eps)) or (l > (min(o, c) + eps)) or (h < (l - eps))

            if is_inverted:
                if self.allow_geometry_repair:
                    h = expected_h
                    l = expected_l
                else:
                    return False, ValidationStatus.INVERTED_OHLC, candle

            normalized = {
                "pair": str(candle.get("pair", "")).upper(),
                "timeframe": str(candle.get("timeframe", "")).lower(),
                "timestamp": ts_ms,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": v,
            }
            return True, ValidationStatus.VALID, normalized

        except (ValueError, TypeError, KeyError) as exc:
            logger.debug("Failed candle parse during validation: %s", exc)
            return False, ValidationStatus.INVALID_PRICE, candle

    def validate_series(
        self,
        candles: list[dict[str, Any]],
        expected_timeframe: str | None = None,
        pair: str = "",
    ) -> CandleValidationResult:
        """
        Validate an entire sequence of candles:
          - Filters individual corrupt candles
          - Sorts chronologically
          - Deduplicates identical timestamps
          - Detects missing interval gaps
          - Flags excessive single-bar flash price jumps
        """
        if not candles:
            return CandleValidationResult(
                is_valid=True,
                cleaned_candles=[],
                corrupted_count=0,
                summary_message="Empty candle series",
            )

        valid_list: list[dict[str, Any]] = []
        corrupted_count = 0
        anomalies: list[dict[str, Any]] = []
        gaps: list[dict[str, Any]] = []

        # 1. Validate individual candles
        for c in candles:
            is_valid, status, normalized = self.validate_candle(c)
            if not is_valid:
                corrupted_count += 1
                anomalies.append({
                    "reason": status.value,
                    "candle": c,
                })
            else:
                valid_list.append(normalized)

        if not valid_list:
            return CandleValidationResult(
                is_valid=False,
                cleaned_candles=[],
                corrupted_count=corrupted_count,
                anomalies=anomalies,
                summary_message=f"All {corrupted_count} candles in series were invalid",
            )

        # 2. Sort chronologically by timestamp
        valid_list.sort(key=lambda x: int(x["timestamp"]))

        # 3. Deduplicate by timestamp (retain latest entry if duplicate)
        deduped: dict[int, dict[str, Any]] = {}
        for c in valid_list:
            deduped[c["timestamp"]] = c
        cleaned = sorted(deduped.values(), key=lambda x: int(x["timestamp"]))

        # 4. Flash Jump Detection & 5. Gap Detection
        tf_key = (expected_timeframe or cleaned[0].get("timeframe", "")).lower()
        expected_step_ms = TIMEFRAME_INTERVAL_MS.get(tf_key)

        for i in range(1, len(cleaned)):
            prev = cleaned[i - 1]
            curr = cleaned[i]

            # Flash jump check (against previous close)
            if prev["close"] > 0:
                jump_ratio = abs(curr["close"] - prev["close"]) / prev["close"]
                if jump_ratio > self.max_jump_ratio:
                    anomalies.append({
                        "reason": ValidationStatus.EXCESSIVE_JUMP.value,
                        "timestamp": curr["timestamp"],
                        "jump_pct": round(jump_ratio * 100.0, 2),
                        "prev_close": prev["close"],
                        "curr_close": curr["close"],
                    })

            # Gap check
            if expected_step_ms:
                dt_ms = curr["timestamp"] - prev["timestamp"]
                # If gap is greater than 1.5x expected interval
                if dt_ms > (expected_step_ms * 1.5):
                    missing_bars = round(dt_ms / expected_step_ms) - 1
                    gaps.append({
                        "pair": pair or curr.get("pair", ""),
                        "timeframe": tf_key,
                        "from_timestamp": prev["timestamp"],
                        "to_timestamp": curr["timestamp"],
                        "gap_ms": dt_ms,
                        "missing_bars": missing_bars,
                    })

        is_overall_valid = (corrupted_count == 0) and (len(anomalies) == 0)
        summary = (
            f"Validated {len(candles)} candles for {pair or 'series'}: "
            f"{len(cleaned)} clean, {corrupted_count} corrupted, "
            f"{len(anomalies)} anomalies, {len(gaps)} gaps."
        )

        return CandleValidationResult(
            is_valid=is_overall_valid,
            cleaned_candles=cleaned,
            corrupted_count=corrupted_count,
            anomalies=anomalies,
            gaps=gaps,
            summary_message=summary,
        )

    def filter_valid_candles(
        self, candles: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Filter and return only structurally valid candles."""
        if not candles:
            return []
        res = self.validate_series(candles)
        return res.cleaned_candles

