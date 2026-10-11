"""Provider-neutral market data values."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from .symbols import StockSymbol


@dataclass(frozen=True, slots=True)
class MarketQuote:
    symbol: StockSymbol
    price: Decimal
    timestamp: datetime
    source: str
    generation: int = 0

    def __post_init__(self) -> None:
        if not self.price.is_finite() or self.price <= 0:
            raise ValueError("INVALID_MARKET_PRICE")
        if self.timestamp.tzinfo is None:
            raise ValueError("MARKET_TIMESTAMP_MUST_BE_TIMEZONE_AWARE")
        object.__setattr__(self, "timestamp", self.timestamp.astimezone(timezone.utc))
        object.__setattr__(self, "source", self.source.strip().upper())
        if self.generation < 0:
            raise ValueError("INVALID_MARKET_GENERATION")

    def is_fresh(self, now: datetime, max_age: timedelta) -> bool:
        if now.tzinfo is None:
            raise ValueError("NOW_MUST_BE_TIMEZONE_AWARE")
        age = now.astimezone(timezone.utc) - self.timestamp
        return timedelta(0) <= age <= max_age
