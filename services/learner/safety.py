"""Immutable safety boundary for learner-generated calibration proposals."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class CalibrationDecision:
    accepted: bool
    code: str
    candidate: dict[str, Any]


class CalibrationSafety:
    NUMERIC_BOUNDS = {
        "weight_multiplier": (Decimal("0.80"), Decimal("1.20")),
        "confluence_threshold": (Decimal("85"), Decimal("95")),
        "liquidity_weight": (Decimal("0"), Decimal("2")),
        "spread_weight": (Decimal("0"), Decimal("2")),
        "momentum_weight": (Decimal("0"), Decimal("2")),
        "relative_strength_weight": (Decimal("0"), Decimal("2")),
        "timeframe_alignment_weight": (Decimal("0"), Decimal("2")),
        "volatility_weight": (Decimal("0"), Decimal("2")),
        "data_quality_weight": (Decimal("0"), Decimal("2")),
    }
    ALLOWED_KEYS = frozenset({"strategy", *NUMERIC_BOUNDS})

    def validate(
        self, candidate: Mapping[str, Any], last_safe: Mapping[str, Any] | None = None
    ) -> CalibrationDecision:
        proposal = dict(candidate)
        unknown = set(proposal) - self.ALLOWED_KEYS
        if unknown:
            return CalibrationDecision(False, "FORBIDDEN_CALIBRATION_KEY", dict(last_safe or {}))
        if "strategy" not in proposal:
            return CalibrationDecision(False, "MISSING_STRATEGY", dict(last_safe or {}))
        for key, (lower, upper) in self.NUMERIC_BOUNDS.items():
            if key not in proposal:
                continue
            try:
                value = Decimal(str(proposal[key]))
            except (InvalidOperation, ValueError, TypeError):
                return CalibrationDecision(False, "INVALID_CALIBRATION_VALUE", dict(last_safe or {}))
            if not value.is_finite() or not lower <= value <= upper:
                return CalibrationDecision(False, "CALIBRATION_OUT_OF_BOUNDS", dict(last_safe or {}))
        return CalibrationDecision(True, "CALIBRATION_SAFE", proposal)
