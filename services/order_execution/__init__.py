"""Disabled-by-default broker-neutral execution foundation."""

from .broker import BrokerAdapter, MockBroker
from .contracts import (
    BrokerOrderUpdate,
    ExecutionStatus,
    OrderIntent,
    OrderSide,
    OrderType,
    ProductType,
    validate_transition,
)

__all__ = [
    "BrokerAdapter",
    "BrokerOrderUpdate",
    "ExecutionStatus",
    "MockBroker",
    "OrderIntent",
    "OrderSide",
    "OrderType",
    "ProductType",
    "validate_transition",
]
