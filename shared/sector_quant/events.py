"""
sector_quant.events — Event-driven primitives for multi-asset backtesting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class EventType(str, Enum):
    MARKET = "MARKET"
    SIGNAL = "SIGNAL"
    ORDER = "ORDER"
    FILL = "FILL"


@dataclass
class Event:
    """Base event representation."""

    type: EventType


@dataclass
class MarketEvent(Event):
    """
    Emitted when a new market bar is received for one or more symbols.
    """

    type: EventType = field(default=EventType.MARKET, init=False)


@dataclass
class SignalEvent(Event):
    """
    Emitted by a Strategy to signal portfolio rebalancing.
    """

    symbol: str
    datetime: datetime
    signal_type: str  # "LONG", "SHORT", "EXIT"
    strength: float = 1.0
    strategy_id: str = "DEFAULT"
    type: EventType = field(default=EventType.SIGNAL, init=False)


@dataclass
class OrderEvent(Event):
    """
    Emitted by Portfolio / RiskEngine to ExecutionHandler.
    """

    symbol: str
    order_type: str  # "MKT", "LMT"
    quantity: int
    direction: str  # "BUY", "SELL"
    price: float | None = None
    type: EventType = field(default=EventType.ORDER, init=False)

    def print_order(self) -> str:
        return f"Order: {self.direction} {self.quantity} shares of {self.symbol} ({self.order_type})"


@dataclass
class FillEvent(Event):
    """
    Encapsulates execution result from broker / simulated exchange.
    """

    timeindex: datetime
    symbol: str
    exchange: str
    quantity: int
    direction: str  # "BUY", "SELL"
    fill_cost: float
    commission: float = 0.0
    slippage: float = 0.0
    type: EventType = field(default=EventType.FILL, init=False)

    @property
    def price_per_share(self) -> float:
        if self.quantity > 0:
            return self.fill_cost / self.quantity
        return 0.0
