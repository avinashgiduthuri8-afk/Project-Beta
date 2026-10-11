"""Deterministic paper fills based solely on Scanner-recorded quotes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from market.models import MarketQuote
from market.symbols import validate_share_quantity


@dataclass(frozen=True, slots=True)
class PaperOrder:
    id: str
    side: str
    order_type: str
    quantity: int
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None


@dataclass(frozen=True, slots=True)
class PaperFill:
    order_id: str
    quantity: int
    price: Decimal
    quote_timestamp: datetime


class PaperFillSimulator:
    def __init__(self, max_quote_age: timedelta = timedelta(minutes=15)) -> None:
        self.max_quote_age = max_quote_age

    def fill(self, order: PaperOrder, quote: MarketQuote, now: datetime) -> PaperFill | None:
        quantity = validate_share_quantity(order.quantity)
        if not quote.is_fresh(now, self.max_quote_age):
            return None
        side, order_type = order.side.upper(), order.order_type.upper()
        if side not in {"BUY", "SELL"} or order_type not in {"MARKET", "LIMIT", "STOP"}:
            raise ValueError("INVALID_PAPER_ORDER")
        eligible = order_type == "MARKET"
        if order_type == "LIMIT" and order.limit_price is not None:
            eligible = quote.price <= order.limit_price if side == "BUY" else quote.price >= order.limit_price
        if order_type == "STOP" and order.stop_price is not None:
            eligible = quote.price >= order.stop_price if side == "BUY" else quote.price <= order.stop_price
        return PaperFill(order.id, quantity, quote.price, quote.timestamp) if eligible else None
