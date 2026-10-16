"""Cheap fail-closed eligibility gates that run before strategy evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from statistics import median
from typing import Any

from market.models import MarketQuote
from market.universe import UniverseInstrument


@dataclass(frozen=True, slots=True)
class EligibilityDecision:
    eligible: bool
    code: str
    penny_stock: bool = False
    evidence: dict[str, Any] = field(default_factory=dict)


class EligibilityEngine:
    def __init__(
        self,
        *,
        max_quote_age: timedelta = timedelta(minutes=15),
        price_ceiling: Decimal = Decimal("250"),
    ) -> None:
        self.max_quote_age = max_quote_age
        self.price_ceiling = price_ceiling

    @staticmethod
    def _turnover(candle: dict[str, Any]) -> Decimal:
        return Decimal(str(candle["close"])) * Decimal(str(candle["volume"]))

    @staticmethod
    def _estimated_spread(intraday: list[dict[str, Any]]) -> Decimal | None:
        if len(intraday) < 20:
            return None
        values = []
        for candle in intraday[-20:]:
            close = Decimal(str(candle["close"]))
            if close <= 0:
                return None
            values.append((Decimal(str(candle["high"])) - Decimal(str(candle["low"]))) / close * 100)
        return Decimal(str(median(values)))

    def evaluate(
        self,
        instrument: UniverseInstrument,
        quote: MarketQuote,
        daily_candles: list[dict[str, Any]],
        intraday_candles: list[dict[str, Any]],
        now: datetime,
    ) -> EligibilityDecision:
        price = quote.price
        penny = price < 1
        if price > self.price_ceiling:
            return EligibilityDecision(False, "PRICE_ABOVE_LIMIT", penny)
        if not quote.is_fresh(now, self.max_quote_age):
            return EligibilityDecision(False, "STALE_QUOTE", penny)
        if instrument.surveillance_status != "NONE":
            return EligibilityDecision(False, "SURVEILLANCE_RESTRICTED", penny)
        if instrument.circuit_locked_sessions >= 2:
            return EligibilityDecision(False, "CIRCUIT_RISK", penny)
        if len(daily_candles) < 20:
            return EligibilityDecision(False, "INSUFFICIENT_HISTORY", penny)
        if len(intraday_candles) < 20:
            return EligibilityDecision(False, "SPREAD_UNKNOWN", penny)
        if any(Decimal(str(candle["volume"])) <= 0 for candle in intraday_candles[-20:]):
            return EligibilityDecision(False, "INSUFFICIENT_LIQUIDITY", penny)
        median_turnover = Decimal(str(median([self._turnover(c) for c in daily_candles[-20:]])))
        minimum_turnover = Decimal("10000000") if penny else Decimal("5000000")
        if median_turnover < minimum_turnover:
            return EligibilityDecision(False, "INSUFFICIENT_LIQUIDITY", penny, {"median_turnover": str(median_turnover)})
        spread = quote.spread_pct()
        if spread is None:
            spread = self._estimated_spread(intraday_candles)
        if spread is None:
            return EligibilityDecision(False, "SPREAD_UNKNOWN", penny)
        maximum_spread = Decimal("1.0") if penny else Decimal("1.5")
        if spread > maximum_spread:
            return EligibilityDecision(False, "SPREAD_TOO_WIDE", penny, {"spread_pct": str(spread)})
        return EligibilityDecision(
            True,
            "ELIGIBLE",
            penny,
            {"median_turnover": str(median_turnover), "spread_pct": str(spread)},
        )
