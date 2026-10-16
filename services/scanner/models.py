"""Immutable scanner ranking values."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from market.symbols import StockSymbol


@dataclass(frozen=True, slots=True)
class RankingEvidence:
    liquidity: Decimal
    spread_quality: Decimal
    momentum: Decimal
    relative_strength: Decimal
    timeframe_alignment: Decimal
    volatility_suitability: Decimal
    data_quality: Decimal

    @property
    def total(self) -> Decimal:
        return sum(
            (
                self.liquidity,
                self.spread_quality,
                self.momentum,
                self.relative_strength,
                self.timeframe_alignment,
                self.volatility_suitability,
                self.data_quality,
            ),
            Decimal("0"),
        )


@dataclass(frozen=True, slots=True)
class ScannerCandidate:
    symbol: StockSymbol
    sector: str
    penny_stock: bool
    evidence_timestamp: datetime
    evidence: RankingEvidence
    affordability_preview: Decimal
    ranking_score: Decimal = Decimal("0")
    reason_codes: tuple[str, ...] = ()
    strategy_results: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ScannerDecision:
    candidate: ScannerCandidate
    accepted: bool
    code: str
    message: str
