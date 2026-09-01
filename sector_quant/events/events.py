"""Core Event Objects for the event-driven quantitative trading framework."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any


class EventType(str, Enum):
    MARKET = "MARKET"
    SIGNAL = "SIGNAL"
    ORDER = "ORDER"
    FILL = "FILL"


class SignalType(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    EXIT = "EXIT"


class OrderType(str, Enum):
    MKT = "MKT"
    LMT = "LMT"


class OrderDirection(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class Event:
    """Base event class."""
    type: EventType = EventType.MARKET


@dataclass
class MarketEvent(Event):
    """MarketEvent emitted by DataHandler on each new synchronized bar or heartbeat."""
    type: EventType = field(default=EventType.MARKET, init=False)
    datetime: Optional[datetime] = None
    symbol: Optional[str] = None
    bar_data: Optional[Dict[str, Any]] = None


@dataclass
class SignalEvent(Event):
    """SignalEvent emitted by a Strategy containing trading intent."""
    strategy_id: str = ""
    symbol: str = ""
    datetime: Optional[datetime] = None
    signal_type: SignalType = SignalType.LONG
    strength: float = 1.0
    target_price: Optional[float] = None
    stop_loss: Optional[float] = None
    meta: Dict[str, Any] = field(default_factory=dict)
    type: EventType = field(default=EventType.SIGNAL, init=False)


@dataclass
class OrderEvent(Event):
    """OrderEvent emitted by the Portfolio risk engine to the execution handler."""
    symbol: str = ""
    order_type: OrderType = OrderType.MKT
    quantity: int = 0
    direction: OrderDirection = OrderDirection.BUY
    datetime: Optional[datetime] = None
    price: Optional[float] = None
    strategy_id: Optional[str] = None
    sector: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)
    type: EventType = field(default=EventType.ORDER, init=False)

    def __repr__(self) -> str:
        return (
            f"OrderEvent(symbol={self.symbol}, type={self.order_type.value}, "
            f"qty={self.quantity}, dir={self.direction.value}, price={self.price})"
        )


@dataclass
class FillEvent(Event):
    """FillEvent emitted by ExecutionHandler upon simulated or live broker fill."""
    timeindex: Optional[datetime] = None
    symbol: str = ""
    exchange: str = "SIMULATED"
    quantity: int = 0
    direction: OrderDirection = OrderDirection.BUY
    fill_price: float = 0.0
    fill_cost: float = 0.0
    commission: float = 0.0
    slippage: float = 0.0
    order_id: Optional[str] = None
    strategy_id: Optional[str] = None
    sector: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)
    type: EventType = field(default=EventType.FILL, init=False)

    def __repr__(self) -> str:
        return (
            f"FillEvent(time={self.timeindex}, sym={self.symbol}, "
            f"qty={self.quantity}, dir={self.direction.value}, "
            f"price={self.fill_price:.2f}, comm={self.commission:.2f}, slip={self.slippage:.2f})"
        )

