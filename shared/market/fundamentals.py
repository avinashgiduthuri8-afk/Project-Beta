"""Normalized, source-neutral stock fundamental data contracts."""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Protocol

from .symbols import StockSymbol


_NUMERIC_FIELDS = (
    "market_cap",
    "pe_ratio",
    "eps",
    "revenue_growth_pct",
    "profit_growth_pct",
    "debt_to_equity",
    "roe_pct",
)


def _optional_decimal(value: object, field_name: str) -> Decimal | None:
    if value is None:
        return None
    try:
        normalized = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"INVALID_FUNDAMENTAL_DATA: {field_name}") from None
    if not normalized.is_finite():
        raise ValueError(f"INVALID_FUNDAMENTAL_DATA: {field_name}")
    if field_name in {"market_cap", "pe_ratio"} and normalized <= 0:
        raise ValueError(f"INVALID_FUNDAMENTAL_DATA: {field_name}")
    if field_name == "debt_to_equity" and normalized < 0:
        raise ValueError(f"INVALID_FUNDAMENTAL_DATA: {field_name}")
    return normalized


@dataclass(frozen=True, slots=True)
class FundamentalData:
    symbol: StockSymbol
    as_of: datetime
    source: str
    market_cap: Decimal | None = None
    pe_ratio: Decimal | None = None
    eps: Decimal | None = None
    revenue_growth_pct: Decimal | None = None
    profit_growth_pct: Decimal | None = None
    debt_to_equity: Decimal | None = None
    roe_pct: Decimal | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, StockSymbol):
            raise ValueError("INVALID_FUNDAMENTAL_DATA: symbol")
        if not isinstance(self.as_of, datetime) or self.as_of.tzinfo is None:
            raise ValueError("INVALID_FUNDAMENTAL_DATA: as_of")
        source = str(self.source).strip().upper()
        if not source:
            raise ValueError("INVALID_FUNDAMENTAL_DATA: source")
        object.__setattr__(self, "source", source)
        for field_name in _NUMERIC_FIELDS:
            object.__setattr__(
                self,
                field_name,
                _optional_decimal(getattr(self, field_name), field_name),
            )

    def is_fresh(self, now: datetime, max_age: timedelta) -> bool:
        if not isinstance(now, datetime) or now.tzinfo is None or max_age < timedelta(0):
            return False
        age = now - self.as_of
        return timedelta(0) <= age <= max_age

    def as_dict(self) -> dict[str, str | None]:
        result: dict[str, str | None] = {
            "symbol": self.symbol.canonical,
            "as_of": self.as_of.isoformat(),
            "source": self.source,
        }
        for field_info in fields(self):
            if field_info.name in _NUMERIC_FIELDS:
                value = getattr(self, field_info.name)
                result[field_info.name] = str(value) if value is not None else None
        return result


class FundamentalDataProvider(Protocol):
    """Source adapter contract; no concrete external source is enabled by default."""

    async def get_fundamentals(self, symbol: StockSymbol) -> FundamentalData | None: ...


__all__ = ["FundamentalData", "FundamentalDataProvider"]
