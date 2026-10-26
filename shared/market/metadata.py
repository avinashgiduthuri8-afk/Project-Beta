"""Market-neutral exchange metadata without provider or execution behavior."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping


class Market(str, Enum):
    INDIA = "INDIA"
    US = "US"


class Exchange(str, Enum):
    NSE = "NSE"
    BSE = "BSE"
    NYSE = "NYSE"
    NASDAQ = "NASDAQ"


class Currency(str, Enum):
    INR = "INR"
    USD = "USD"


class IdentifierType(str, Enum):
    SECURITY_ID = "SECURITY_ID"
    TICKER = "TICKER"


class QuantityPolicy(str, Enum):
    WHOLE_ONLY = "WHOLE_ONLY"
    FRACTIONAL_ALLOWED = "FRACTIONAL_ALLOWED"


@dataclass(frozen=True, slots=True)
class ExchangeMetadata:
    market: Market
    exchange: Exchange
    timezone: str
    currency: Currency
    identifier_type: IdentifierType
    quantity_policy: QuantityPolicy


EXCHANGE_METADATA: Mapping[Exchange, ExchangeMetadata] = MappingProxyType(
    {
        Exchange.NSE: ExchangeMetadata(
            Market.INDIA,
            Exchange.NSE,
            "Asia/Kolkata",
            Currency.INR,
            IdentifierType.SECURITY_ID,
            QuantityPolicy.WHOLE_ONLY,
        ),
        Exchange.BSE: ExchangeMetadata(
            Market.INDIA,
            Exchange.BSE,
            "Asia/Kolkata",
            Currency.INR,
            IdentifierType.SECURITY_ID,
            QuantityPolicy.WHOLE_ONLY,
        ),
        Exchange.NYSE: ExchangeMetadata(
            Market.US,
            Exchange.NYSE,
            "America/New_York",
            Currency.USD,
            IdentifierType.TICKER,
            QuantityPolicy.FRACTIONAL_ALLOWED,
        ),
        Exchange.NASDAQ: ExchangeMetadata(
            Market.US,
            Exchange.NASDAQ,
            "America/New_York",
            Currency.USD,
            IdentifierType.TICKER,
            QuantityPolicy.FRACTIONAL_ALLOWED,
        ),
    }
)


def get_exchange_metadata(exchange: str | Exchange) -> ExchangeMetadata:
    try:
        normalized = exchange if isinstance(exchange, Exchange) else Exchange(str(exchange).strip().upper())
    except ValueError:
        raise ValueError("UNSUPPORTED_STOCK_EXCHANGE") from None
    return EXCHANGE_METADATA[normalized]
