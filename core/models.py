"""Domain data models for Indian Market Algorithmic Trading."""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field
from core.enums import Exchange, ProductType, OrderType, OrderSide, OrderStatus, TimeInForce


class Tick(BaseModel):
    token: str
    symbol: str
    exchange: Exchange = Exchange.NSE
    ltp: float                          # Last Traded Price
    volume: int = 0
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    bid_price: Optional[float] = None
    ask_price: Optional[float] = None
    timestamp: datetime = Field(default_factory=datetime.now)


class Candle(BaseModel):
    symbol: str
    timeframe_minutes: int
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 0
    vwap: Optional[float] = None
    is_closed: bool = False


class OrderRequest(BaseModel):
    client_order_id: str
    symbol: str
    exchange: Exchange = Exchange.NSE
    side: OrderSide
    order_type: OrderType
    product_type: ProductType = ProductType.MIS
    quantity: int
    price: Optional[float] = None
    trigger_price: Optional[float] = None
    time_in_force: TimeInForce = TimeInForce.DAY
    tag: Optional[str] = "ProjectBeta"


class Order(BaseModel):
    order_id: str
    client_order_id: str
    symbol: str
    exchange: Exchange = Exchange.NSE
    side: OrderSide
    order_type: OrderType
    product_type: ProductType = ProductType.MIS
    quantity: int
    filled_quantity: int = 0
    pending_quantity: int = 0
    price: Optional[float] = None
    average_price: float = 0.0
    trigger_price: Optional[float] = None
    status: OrderStatus = OrderStatus.PENDING
    status_message: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class Trade(BaseModel):
    trade_id: str
    order_id: str
    symbol: str
    side: OrderSide
    quantity: int
    price: float
    value: float
    timestamp: datetime = Field(default_factory=datetime.now)


class Position(BaseModel):
    symbol: str
    exchange: Exchange = Exchange.NSE
    product_type: ProductType = ProductType.MIS
    quantity: int = 0                  # Positive for Long, Negative for Short
    buy_quantity: int = 0
    sell_quantity: int = 0
    buy_value: float = 0.0
    sell_value: float = 0.0
    average_buy_price: float = 0.0
    average_sell_price: float = 0.0
    ltp: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    total_pnl: float = 0.0


class AccountBalance(BaseModel):
    total_capital: float
    available_margin: float
    utilized_margin: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
