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
    bid: Decimal | None = None
    ask: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.price.is_finite() or self.price <= 0:
            raise ValueError("INVALID_MARKET_PRICE")
        if self.timestamp.tzinfo is None:
            raise ValueError("MARKET_TIMESTAMP_MUST_BE_TIMEZONE_AWARE")
        object.__setattr__(self, "timestamp", self.timestamp.astimezone(timezone.utc))
        object.__setattr__(self, "source", self.source.strip().upper())
        for name in ("bid", "ask"):
            value = getattr(self, name)
            if value is not None:
                value = Decimal(str(value))
                if not value.is_finite() or value <= 0:
                    raise ValueError("INVALID_MARKET_SPREAD")
                object.__setattr__(self, name, value)
        if self.bid is not None and self.ask is not None and self.ask < self.bid:
            raise ValueError("INVALID_MARKET_SPREAD")
        if self.generation < 0:
            raise ValueError("INVALID_MARKET_GENERATION")

    def is_fresh(self, now: datetime, max_age: timedelta) -> bool:
        if now.tzinfo is None:
            raise ValueError("NOW_MUST_BE_TIMEZONE_AWARE")
        age = now.astimezone(timezone.utc) - self.timestamp
        return timedelta(0) <= age <= max_age

    def spread_pct(self) -> Decimal | None:
        if self.bid is None or self.ask is None:
            return None
        midpoint = (self.bid + self.ask) / 2
        return (self.ask - self.bid) / midpoint * 100 if midpoint > 0 else None
