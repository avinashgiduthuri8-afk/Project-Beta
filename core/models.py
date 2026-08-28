"""Domain data models for Indian Market Algorithmic Trading (Project-Beta)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field
from core.enums import (
    Exchange, ProductType, OrderType, OrderSide, OrderStatus,
    MarketSession, MarketRegime, SetupType, Timeframe, AISignalDecision
)


class Tick(BaseModel):
    token: str
    symbol: str
    exchange: Exchange = Exchange.NSE
    ltp: float
    volume: int = 0
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    bid_price: Optional[float] = None
    ask_price: Optional[float] = None
    delivery_pct: Optional[float] = None
    timestamp: datetime = Field(default_factory=datetime.now)


class Candle(BaseModel):
    symbol: str
    timeframe: str = "5m"
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 0
    delivery_volume: Optional[int] = None
    vwap: Optional[float] = None
    is_closed: bool = True


class ScoreBreakdown(BaseModel):
    trend_regime_score: float = 0.0        # Max 25
    setup_geometry_score: float = 0.0      # Max 25
    volume_delivery_score: float = 0.0     # Max 25
    mtb_confirmation_score: float = 0.0    # Max 25
    total_score: float = 0.0               # Max 100
    hard_gates_passed: bool = True
    rejection_reason: Optional[str] = None


class ScannerCandidate(BaseModel):
    symbol: str
    exchange: Exchange = Exchange.NSE
    sector: str = "GENERAL"
    ltp: float
    setup_type: SetupType
    score_breakdown: ScoreBreakdown
    relative_strength_vs_nifty: float = 0.0
    daily_volume: int = 0
    delivery_pct: float = 0.0
    atr_14: float = 0.0
    ema_20: float = 0.0
    ema_50: float = 0.0
    ema_200: float = 0.0
    timestamp: datetime = Field(default_factory=datetime.now)


class AISignalEvaluation(BaseModel):
    symbol: str
    decision: AISignalDecision = AISignalDecision.CONFIRMED
    confidence_score: float = 0.8          # 0.0 to 1.0
    primary_catalyst: str = ""
    counter_evidence: List[str] = Field(default_factory=list)
    invalidation_price: float = 0.0
    recommended_rr_ratio: float = 2.0
    timestamp: datetime = Field(default_factory=datetime.now)


class TradePlan(BaseModel):
    plan_id: str
    symbol: str
    exchange: Exchange = Exchange.NSE
    side: OrderSide = OrderSide.BUY
    product_type: ProductType = ProductType.MIS
    setup_type: SetupType
    entry_price: float
    stop_loss: float
    target_price: float
    risk_per_share: float
    reward_per_share: float
    risk_reward_ratio: float
    calculated_quantity: int
    total_capital_required: float
    ai_confidence: float = 1.0
    timestamp: datetime = Field(default_factory=datetime.now)


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
    quantity: int = 0
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
    sector: str = "GENERAL"


class AccountBalance(BaseModel):
    total_capital: float
    available_margin: float
    utilized_margin: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0


class SystemHealthStatus(BaseModel):
    data_feed_healthy: bool = True
    last_tick_latency_ms: float = 50.0
    market_session: MarketSession = MarketSession.NORMAL
    open_positions_count: int = 0
    active_circuit_breaker: bool = False
    broker_authenticated: bool = True
    timestamp: datetime = Field(default_factory=datetime.now)
