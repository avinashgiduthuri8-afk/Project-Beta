"""Deterministic broad ranking before expensive strategy evaluation."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any, Sequence

from .models import ScannerCandidate


class CandidateRanker:
    def rank(
        self,
        candidates: Sequence[ScannerCandidate],
        market_context: dict[str, Any],
    ) -> list[ScannerCandidate]:
        regime_factor = Decimal("1") if market_context.get("regime") == "BULL" else Decimal("0.8")
        scored = [
            replace(candidate, ranking_score=candidate.evidence.total * regime_factor)
            for candidate in candidates
        ]
        return sorted(
            scored,
            key=lambda item: (-item.ranking_score, item.symbol.canonical),
        )[:50]
