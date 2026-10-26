"""Canonical identifiers and quantity rules for Indian cash equities."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal
import re

from .metadata import (
    Currency,
    ExchangeMetadata,
    IdentifierType,
    Market,
    QuantityPolicy,
    get_exchange_metadata,
)

_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9&.-]{0,29}$")


@dataclass(frozen=True, slots=True)
class StockSymbol:
    exchange: Literal["NSE", "BSE"]
    trading_symbol: str
    security_id: str | None = None

    def __post_init__(self) -> None:
        exchange = str(self.exchange).upper()
        trading_symbol = str(self.trading_symbol).strip().upper()
        if exchange not in {"NSE", "BSE"} or not _SYMBOL.fullmatch(trading_symbol):
            raise ValueError("INVALID_STOCK_SYMBOL")
        object.__setattr__(self, "exchange", exchange)
        object.__setattr__(self, "trading_symbol", trading_symbol)

    @property
    def canonical(self) -> str:
        return f"{self.exchange}:{self.trading_symbol}"

    @property
    def metadata(self) -> ExchangeMetadata:
        return get_exchange_metadata(self.exchange)

    @property
    def market(self) -> Market:
        return self.metadata.market

    @property
    def timezone(self) -> str:
        return self.metadata.timezone

    @property
    def currency(self) -> Currency:
        return self.metadata.currency

    @property
    def identifier_type(self) -> IdentifierType:
        return self.metadata.identifier_type

    @property
    def quantity_policy(self) -> QuantityPolicy:
        return self.metadata.quantity_policy

    def __str__(self) -> str:
        return self.canonical

    @classmethod
    def parse(cls, raw: str, default_exchange: str = "NSE") -> "StockSymbol":
        value = str(raw or "").strip().upper()
        exchange = str(default_exchange).strip().upper()
        if value.endswith(".NS"):
            exchange, value = "NSE", value[:-3]
        elif value.endswith(".BO"):
            exchange, value = "BSE", value[:-3]
        elif ":" in value:
            exchange, value = value.split(":", 1)
        if exchange not in {"NSE", "BSE"} or not _SYMBOL.fullmatch(value):
            raise ValueError("INVALID_STOCK_SYMBOL")
        return cls(exchange=exchange, trading_symbol=value)


def validate_share_quantity(value: object) -> int:
    """Return a positive integral share count, rejecting non-finite values."""
    try:
        quantity = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("INVALID_SHARE_QUANTITY") from None
    if not quantity.is_finite() or quantity <= 0 or quantity != quantity.to_integral_value():
        raise ValueError("INVALID_SHARE_QUANTITY")
    return int(quantity)
