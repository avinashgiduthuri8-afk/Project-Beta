"""Broker-neutral order contracts with a fail-closed lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
import re

from market.symbols import StockSymbol, validate_share_quantity


_CORRELATION_ID = re.compile(r"^[A-Za-z0-9 _-]{1,30}$")


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_LOSS = "STOP_LOSS"
    STOP_LOSS_MARKET = "STOP_LOSS_MARKET"


class ProductType(str, Enum):
    CNC = "CNC"


class ExecutionStatus(str, Enum):
    CREATED = "CREATED"
    RISK_APPROVED = "RISK_APPROVED"
    SUBMITTING = "SUBMITTING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PENDING = "PENDING"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


_TRANSITIONS: dict[ExecutionStatus, frozenset[ExecutionStatus]] = {
    ExecutionStatus.CREATED: frozenset(
        {ExecutionStatus.RISK_APPROVED, ExecutionStatus.REJECTED, ExecutionStatus.CANCELLED}
    ),
    ExecutionStatus.RISK_APPROVED: frozenset(
        {ExecutionStatus.SUBMITTING, ExecutionStatus.CANCELLED}
    ),
    ExecutionStatus.SUBMITTING: frozenset(
        {
            ExecutionStatus.ACKNOWLEDGED,
            ExecutionStatus.PENDING,
            ExecutionStatus.PARTIALLY_FILLED,
            ExecutionStatus.FILLED,
            ExecutionStatus.REJECTED,
            ExecutionStatus.RECONCILIATION_REQUIRED,
        }
    ),
    ExecutionStatus.ACKNOWLEDGED: frozenset(
        {
            ExecutionStatus.PENDING,
            ExecutionStatus.PARTIALLY_FILLED,
            ExecutionStatus.FILLED,
            ExecutionStatus.REJECTED,
            ExecutionStatus.CANCELLED,
            ExecutionStatus.EXPIRED,
            ExecutionStatus.RECONCILIATION_REQUIRED,
        }
    ),
    ExecutionStatus.PENDING: frozenset(
        {
            ExecutionStatus.PARTIALLY_FILLED,
            ExecutionStatus.FILLED,
            ExecutionStatus.REJECTED,
            ExecutionStatus.CANCELLED,
            ExecutionStatus.EXPIRED,
            ExecutionStatus.RECONCILIATION_REQUIRED,
        }
    ),
    ExecutionStatus.PARTIALLY_FILLED: frozenset(
        {
            ExecutionStatus.PARTIALLY_FILLED,
            ExecutionStatus.FILLED,
            ExecutionStatus.CANCELLED,
            ExecutionStatus.EXPIRED,
            ExecutionStatus.RECONCILIATION_REQUIRED,
        }
    ),
    ExecutionStatus.RECONCILIATION_REQUIRED: frozenset(
        {
            ExecutionStatus.ACKNOWLEDGED,
            ExecutionStatus.PENDING,
            ExecutionStatus.PARTIALLY_FILLED,
            ExecutionStatus.FILLED,
            ExecutionStatus.REJECTED,
            ExecutionStatus.CANCELLED,
            ExecutionStatus.EXPIRED,
        }
    ),
    ExecutionStatus.FILLED: frozenset(),
    ExecutionStatus.REJECTED: frozenset(),
    ExecutionStatus.CANCELLED: frozenset(),
    ExecutionStatus.EXPIRED: frozenset(),
}


def normalize_execution_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("EXECUTION_TIMESTAMP_MUST_BE_TIMEZONE_AWARE")
    return value.astimezone(timezone.utc)


def _optional_price(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    try:
        price = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("INVALID_EXECUTION_PRICE") from None
    if not price.is_finite() or price <= 0:
        raise ValueError("INVALID_EXECUTION_PRICE")
    return price


def validate_transition(current: ExecutionStatus, target: ExecutionStatus) -> None:
    if target not in _TRANSITIONS[current]:
        raise ValueError(f"INVALID_EXECUTION_TRANSITION:{current.value}->{target.value}")


@dataclass(frozen=True, slots=True)
class OrderIntent:
    intent_id: str
    signal_id: str
    correlation_id: str
    symbol: StockSymbol
    side: OrderSide
    quantity: int
    created_at: datetime
    order_type: OrderType = OrderType.MARKET
    product_type: ProductType = ProductType.CNC
    limit_price: Decimal | None = None
    trigger_price: Decimal | None = None

    def __post_init__(self) -> None:
        if not str(self.intent_id).strip() or not str(self.signal_id).strip():
            raise ValueError("INVALID_EXECUTION_IDENTIFIER")
        correlation_id = str(self.correlation_id).strip()
        if not _CORRELATION_ID.fullmatch(correlation_id):
            raise ValueError("INVALID_CORRELATION_ID")
        if not isinstance(self.symbol, StockSymbol) or not str(
            self.symbol.security_id or ""
        ).strip():
            raise ValueError("EXECUTION_SECURITY_ID_REQUIRED")
        symbol = StockSymbol(
            self.symbol.exchange,
            self.symbol.trading_symbol,
            str(self.symbol.security_id).strip(),
        )
        try:
            side = self.side if isinstance(self.side, OrderSide) else OrderSide(self.side)
            order_type = self.order_type if isinstance(self.order_type, OrderType) else OrderType(self.order_type)
            product_type = self.product_type if isinstance(self.product_type, ProductType) else ProductType(self.product_type)
        except ValueError:
            raise ValueError("INVALID_EXECUTION_ORDER_ENUM") from None
        quantity = validate_share_quantity(self.quantity)
        created_at = normalize_execution_timestamp(self.created_at)
        limit_price = _optional_price(self.limit_price)
        trigger_price = _optional_price(self.trigger_price)
        if order_type is OrderType.LIMIT and limit_price is None:
            raise ValueError("LIMIT_PRICE_REQUIRED")
        if order_type in {OrderType.STOP_LOSS, OrderType.STOP_LOSS_MARKET} and trigger_price is None:
            raise ValueError("TRIGGER_PRICE_REQUIRED")
        object.__setattr__(self, "intent_id", str(self.intent_id).strip())
        object.__setattr__(self, "signal_id", str(self.signal_id).strip())
        object.__setattr__(self, "correlation_id", correlation_id)
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "order_type", order_type)
        object.__setattr__(self, "product_type", product_type)
        object.__setattr__(self, "limit_price", limit_price)
        object.__setattr__(self, "trigger_price", trigger_price)


@dataclass(frozen=True, slots=True)
class BrokerOrderUpdate:
    correlation_id: str
    broker_order_id: str
    status: ExecutionStatus
    requested_quantity: int
    filled_quantity: int
    average_price: Decimal | None
    occurred_at: datetime
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        correlation_id = str(self.correlation_id).strip()
        if not _CORRELATION_ID.fullmatch(correlation_id):
            raise ValueError("INVALID_CORRELATION_ID")
        if not str(self.broker_order_id).strip():
            raise ValueError("BROKER_ORDER_ID_REQUIRED")
        try:
            status = self.status if isinstance(self.status, ExecutionStatus) else ExecutionStatus(self.status)
        except ValueError:
            raise ValueError("INVALID_EXECUTION_STATUS") from None
        requested = validate_share_quantity(self.requested_quantity)
        if isinstance(self.filled_quantity, bool) or not isinstance(self.filled_quantity, int):
            raise ValueError("INVALID_FILLED_QUANTITY")
        filled = self.filled_quantity
        if not 0 <= filled <= requested:
            raise ValueError("INVALID_FILLED_QUANTITY")
        if status is ExecutionStatus.PARTIALLY_FILLED and not 0 < filled < requested:
            raise ValueError("INVALID_PARTIAL_FILL")
        if status is ExecutionStatus.FILLED and filled != requested:
            raise ValueError("INCOMPLETE_FILLED_ORDER")
        average_price = _optional_price(self.average_price)
        if filled > 0 and average_price is None:
            raise ValueError("AVERAGE_PRICE_REQUIRED")
        object.__setattr__(self, "correlation_id", correlation_id)
        object.__setattr__(self, "broker_order_id", str(self.broker_order_id).strip())
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "requested_quantity", requested)
        object.__setattr__(self, "filled_quantity", filled)
        object.__setattr__(self, "average_price", average_price)
        object.__setattr__(
            self, "occurred_at", normalize_execution_timestamp(self.occurred_at)
        )
