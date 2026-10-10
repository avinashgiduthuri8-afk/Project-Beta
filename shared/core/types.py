"""
PROJECT-ALPHA Domain Types.

Single canonical definition of domain concepts used across PROJECT-ALPHA.
No business logic here — pure data containers and enumerations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

# ── Enumerations ──────────────────────────────────────────────────────────────


class BotName(str, Enum):
    STE = "STE"
    HDA = "HDA"
    VCP = "VCP"
    BBS = "BBS"
    MTB = "MTB"
    NR7 = "NR7"
    PPA = "PPA"
    MRB = "MRB"


class BotMode(str, Enum):
    PAPER = "PAPER"
    LIVE = "LIVE"
    DISABLED = "DISABLED"
    PAUSED = "PAUSED"


class BotStatus(str, Enum):
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    DISABLED = "DISABLED"
    ERROR = "ERROR"


class Priority(str, Enum):
    ELITE = "Elite"
    HIGH = "High"
    MEDIUM = "Medium"
    WATCH = "Watch"
    IGNORE = "Ignore"

    @classmethod
    def from_score(cls, score: int) -> Priority:
        if score >= 90:
            return cls.ELITE
        if score >= 80:
            return cls.HIGH
        if score >= 70:
            return cls.MEDIUM
        if score >= 60:
            return cls.WATCH
        return cls.IGNORE

    def gte(self, other: Priority) -> bool:
        _order = [
            Priority.IGNORE,
            Priority.WATCH,
            Priority.MEDIUM,
            Priority.HIGH,
            Priority.ELITE,
        ]
        return _order.index(self) >= _order.index(other)


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class MarketState(str, Enum):
    BREAKOUT = "breakout"
    BULL_TREND = "bull_trend"
    PULLBACK = "pullback"
    RECOVERY = "recovery"
    DOWNTREND = "downtrend"
    SIDEWAYS = "sideways"


class OppType(str, Enum):
    MOMENTUM_TRADE = "momentum_trade"
    CONTINUATION = "continuation"
    ACCUMULATION = "accumulation"
    RECOVERY_TRADE = "recovery_trade"
    WATCHLIST = "watchlist"
    AVOID = "avoid"


class ExitReason(str, Enum):
    TAKE_PROFIT = "TAKE_PROFIT"
    STOP_LOSS = "STOP_LOSS"
    MANUAL = "MANUAL"
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER"


class PositionStatus(str, Enum):
    PENDING_ENTRY = "PENDING_ENTRY"
    OPEN = "OPEN"
    PENDING_EXIT = "PENDING_EXIT"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"
    DESYNCED_MISSING_BALANCE = "DESYNCED_MISSING_BALANCE"


class OrderState(str, Enum):
    CREATED = "CREATED"
    SUBMITTED = "SUBMITTED"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class AIRecommendation(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    SCALE_DOWN = "SCALE_DOWN"
    WATCH = "WATCH"


# ── Domain Objects ────────────────────────────────────────────────────────────


@dataclass
class ConfluenceData:
    """Breakdown of the 5 evaluation layers in C2 High-Conviction Scanner."""

    chart_score: int = 0  # Layer 1: Chart Structure
    indicator_score: int = 0  # Layer 2: Technical Indicators
    sentiment_score: int = 0  # Layer 3: Big-Coin / Market Sentiment
    news_score: int = 0  # Layer 4: News & Events
    confluence_score: int = 0  # Layer 5: Combined Confluence
    is_rejected: bool = False
    rejection_reasons: list[str] = field(default_factory=list)


@dataclass
class Signal:
    id: str
    coin: str
    pair: str
    market_state: MarketState
    opportunity_type: OppType
    priority: Priority
    risk_level: RiskLevel
    score: int  # 0–100
    confidence: int  # 0–100
    coin_class: str | None  # "A"|"B"|"C"
    mtf_alignment: bool
    generated_at: datetime
    expires_at: datetime
    mode: BotMode = BotMode.PAPER
    source_bot: str = "scanner_v1"
    raw_payload: dict = field(default_factory=dict)
    confluence_breakdown: dict | None = field(default_factory=dict)

    @property
    def is_live(self) -> bool:
        from datetime import timezone

        return datetime.now(timezone.utc) < self.expires_at

    @property
    def is_actionable(self) -> bool:
        return self.is_live and self.priority in (
            Priority.ELITE,
            Priority.HIGH,
            Priority.MEDIUM,
        )


@dataclass
class Position:
    id: str
    bot: BotName
    coin: str
    pair: str
    qty: float
    entry_price: float
    entry_time: datetime
    mode: BotMode
    status: PositionStatus = PositionStatus.OPEN
    current_price: float | None = None
    unrealised_pnl: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    signal_id: str | None = None
    closed_at: datetime | None = None
    exit_price: float | None = None
    exit_reason: ExitReason | None = None
    exchange_order_id: str | None = None
    client_order_id: str | None = None
    filled_qty: float | None = None
    realized_pnl: float | None = None
    stop_loss_order_id: str | None = None
    peak_price: float | None = None
    trough_price: float | None = None
    market_regime: str | None = None

    @property
    def deployed_capital(self) -> float:
        base = self.qty * self.entry_price
        if self.pair and (
            self.pair.upper().endswith("USDT")
            or self.pair.upper().endswith("/USDT")
            or self.pair.upper().endswith("_USDT")
            or self.pair.upper().endswith("USD")
            or self.pair.upper().endswith("/USD")
            or self.pair.upper().endswith("_USD")
        ):
            return base * 91.50
        return base


@dataclass
class Trade:
    id: str
    position_id: str
    bot: BotName
    coin: str
    pair: str
    entry_price: float
    exit_price: float
    qty: float
    pnl: float
    pnl_pct: float
    entry_time: datetime
    exit_time: datetime
    exit_reason: ExitReason
    mode: BotMode
    signal_id: str | None = None
    exchange_order_id: str | None = None
    client_order_id: str | None = None


@dataclass
class Order:
    id: str
    client_order_id: str
    bot: BotName
    coin: str
    pair: str
    side: str  # "BUY" or "SELL"
    order_type: str  # "LIMIT" or "MARKET"
    req_qty: float
    price: float
    filled_qty: float = 0.0
    remaining_qty: float = 0.0
    avg_price: float = 0.0
    state: OrderState = OrderState.CREATED
    exchange_order_id: str | None = None
    position_id: str | None = None
    signal_id: str | None = None
    mode: BotMode = BotMode.LIVE
    created_at: datetime = field(default_factory=lambda: datetime.now())
    updated_at: datetime = field(default_factory=lambda: datetime.now())
    error_message: str | None = None

    def __post_init__(self):
        if self.remaining_qty == 0.0 and self.req_qty > 0.0 and self.filled_qty == 0.0:
            self.remaining_qty = self.req_qty


@dataclass
class OrderStateTransition:
    id: str
    order_id: str
    from_state: OrderState
    to_state: OrderState
    timestamp: datetime
    reason: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class BotSnapshot:
    bot: BotName
    mode: BotMode
    status: BotStatus
    cash_balance: float
    deployed_capital: float
    open_positions: int
    total_pnl: float
    last_cycle_at: datetime | None
    health_score: int  # 0–100
    captured_at: datetime


@dataclass
class PortfolioSnapshot:
    total_aum: float
    total_deployed: float
    total_cash: float
    total_unrealised_pnl: float
    total_realised_pnl: float
    daily_pnl: float
    capital_utilisation: float  # percent
    positions_by_bot: dict  # BotName → list[Position]
    captured_at: datetime


@dataclass
class AIAnalysis:
    id: str
    signal_id: str
    coin: str
    pair: str
    recommendation: AIRecommendation
    confidence_score: int  # 0–100
    trend_evaluation: str
    momentum_evaluation: str
    volume_evaluation: str
    setup_quality: str
    market_regime: str
    risk_reward_assessment: str
    supporting_factors: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    risk_factors: list[str] = field(default_factory=list)
    suggested_adjustments: dict = field(default_factory=dict)
    model_name: str = "heuristic-fallback"
    execution_latency_ms: float = 0.0
    analyzed_at: datetime = field(default_factory=lambda: datetime.now())
    raw_response: dict = field(default_factory=dict)


# ── Risk & Decision Types (Phase 5) ──────────────────────────────────────────


@dataclass
class RiskDecision:
    allowed: bool
    code: str  # "ALLOWED", "BLOCKED_BOT_CAPITAL", "BLOCKED_TOTAL_CAPITAL", "BLOCKED_MAX_POSITIONS", "BLOCKED_CIRCUIT_BREAKER", "BLOCKED_DISABLED"
    reason: str
    bot: BotName
    amount: float
    adjusted_amount: float
    check_ms: float = 0.0


@dataclass
class RiskState:
    trading_enabled: bool
    emergency_stop: bool
    circuit_breaker_open: bool
    per_bot_deployed: dict[str, float] = field(default_factory=dict)
    per_bot_open_count: dict[str, int] = field(default_factory=dict)
    daily_pnl: dict[str, float] = field(default_factory=dict)
    last_checked_at: datetime | None = None


# ── Shadow Mode & Divergence Types (Phase 6) ─────────────────────────────────


@dataclass
class ShadowTrade:
    id: str
    signal_id: str
    bot: BotName
    coin: str
    pair: str
    entry_price: float
    qty: float
    amount: float
    stop_loss: float | None = None
    take_profit: float | None = None
    ai_recommendation: str | None = None
    status: str = "OPEN"  # OPEN, CLOSED_TP, CLOSED_SL, EXPIRED, CANCELLED
    simulated_exit_price: float | None = None
    simulated_pnl: float | None = None
    simulated_pnl_pct: float | None = None
    exit_reason: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now())
    closed_at: datetime | None = None
    raw_adjustments: dict = field(default_factory=dict)


@dataclass
class DecisionDivergence:
    id: str
    signal_id: str
    bot: BotName
    coin: str
    v1_action: str  # "EXECUTED", "REJECTED", "NOT_EVALUATED"
    v2_action: str  # "APPROVED", "DENIED", "AI_REJECTED", "SCALED_DOWN"
    divergence_type: (
        str  # "AI_FILTERED", "RISK_FILTERED", "SIZE_SCALED", "TIMING_OFFSET"
    )
    reason: str
    detected_at: datetime = field(default_factory=lambda: datetime.now())
    v1_pnl: float | None = None
    v2_simulated_pnl: float | None = None
