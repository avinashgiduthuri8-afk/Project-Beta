"""Deterministic broad ranking before expensive strategy evaluation."""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from .models import ScannerCandidate


_FUNDAMENTAL_FIELDS = (
    "market_cap",
    "pe_ratio",
    "eps",
    "revenue_growth_pct",
    "profit_growth_pct",
    "debt_to_equity",
    "roe_pct",
)
_LOWER_IS_BETTER = frozenset({"pe_ratio", "debt_to_equity"})


@dataclass(frozen=True, slots=True)
class FundamentalRankingWeights:
    market_cap: Decimal = Decimal("0")
    pe_ratio: Decimal = Decimal("0")
    eps: Decimal = Decimal("0")
    revenue_growth_pct: Decimal = Decimal("0")
    profit_growth_pct: Decimal = Decimal("0")
    debt_to_equity: Decimal = Decimal("0")
    roe_pct: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        total = Decimal("0")
        for field_info in fields(self):
            try:
                value = Decimal(str(getattr(self, field_info.name)))
            except (InvalidOperation, TypeError, ValueError):
                raise ValueError("INVALID_FUNDAMENTAL_WEIGHTS") from None
            if not value.is_finite() or value < 0 or value > 100:
                raise ValueError("INVALID_FUNDAMENTAL_WEIGHTS")
            object.__setattr__(self, field_info.name, value)
            total += value
        if total > 100:
            raise ValueError("INVALID_FUNDAMENTAL_WEIGHTS")

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> "FundamentalRankingWeights":
        if not isinstance(values, Mapping) or set(values) - set(_FUNDAMENTAL_FIELDS):
            raise ValueError("INVALID_FUNDAMENTAL_WEIGHTS")
        return cls(**values)

    @property
    def enabled(self) -> bool:
        return any(getattr(self, name) > 0 for name in _FUNDAMENTAL_FIELDS)


class CandidateRanker:
    def __init__(
        self,
        fundamental_weights: FundamentalRankingWeights | None = None,
        fundamental_max_age: timedelta = timedelta(days=7),
    ) -> None:
        if fundamental_max_age < timedelta(0):
            raise ValueError("INVALID_FUNDAMENTAL_MAX_AGE")
        self.fundamental_weights = fundamental_weights or FundamentalRankingWeights()
        self.fundamental_max_age = fundamental_max_age

    def rank(
        self,
        candidates: Sequence[ScannerCandidate],
        market_context: dict[str, Any],
    ) -> list[ScannerCandidate]:
        regime_factor = Decimal("1") if market_context.get("regime") == "BULL" else Decimal("0.8")
        fundamental_scores = self._fundamental_scores(candidates)
        scored = []
        for candidate in candidates:
            fundamental_score = fundamental_scores.get(candidate.symbol.canonical, Decimal("0"))
            scored.append(
                replace(
                    candidate,
                    fundamental_score=fundamental_score,
                    ranking_score=(candidate.evidence.total * regime_factor) + fundamental_score,
                )
            )
        return sorted(
            scored,
            key=lambda item: (-item.ranking_score, item.symbol.canonical),
        )[:50]

    def _fundamental_scores(
        self, candidates: Sequence[ScannerCandidate]
    ) -> dict[str, Decimal]:
        scores = {candidate.symbol.canonical: Decimal("0") for candidate in candidates}
        if not self.fundamental_weights.enabled:
            return scores

        for field_name in _FUNDAMENTAL_FIELDS:
            weight = getattr(self.fundamental_weights, field_name)
            if weight == 0:
                continue
            values: dict[str, Decimal] = {}
            for candidate in candidates:
                data = candidate.fundamentals
                if (
                    data is None
                    or data.symbol != candidate.symbol
                    or not data.is_fresh(candidate.evidence_timestamp, self.fundamental_max_age)
                ):
                    continue
                value = getattr(data, field_name)
                if value is None or value < 0 or (field_name != "debt_to_equity" and value == 0):
                    continue
                values[candidate.symbol.canonical] = value
            if not values:
                continue

            ordered = sorted(set(values.values()))
            denominator = Decimal(max(1, len(ordered) - 1))
            for canonical, value in values.items():
                position = Decimal(ordered.index(value))
                quality = Decimal("1") if len(ordered) == 1 else position / denominator
                if field_name in _LOWER_IS_BETTER:
                    quality = Decimal("1") - quality
                scores[canonical] += weight * quality
        return scores


__all__ = ["CandidateRanker", "FundamentalRankingWeights"]
