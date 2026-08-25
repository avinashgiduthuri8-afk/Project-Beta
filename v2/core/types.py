"""
V2 Domain Types.

Single canonical definition of every domain concept used across V2.
No business logic here — pure data containers and enumerations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


# ── Enumerations ──────────────────────────────────────────────────────────────

class BotName(str, Enum):
    MTB = "MTB"
    PMB = "PMB"
    VGX = "VGX"


class BotMode(str, Enum):
    PAPER    = "PAPER"
    LIVE     = "LIVE"
    DISABLED = "DISABLED"
    PAUSED   = "PAUSED"


class BotStatus(str, Enum):
    RUNNING  = "RUNNING"
    PAUSED   = "PAUSED"
    DISABLED = "DISABLED"
    ERROR    = "ERROR"


class Priority(str, Enum):
    ELITE  = "Elite"
    HIGH   = "High"
    MEDIUM = "Medium"
    WATCH  = "Watch"
    IGNORE = "Ignore"

    @classmethod
    def from_score(cls, score: int) -> "Priority":
        if score >= 90: return cls.ELITE
        if score >= 80: return cls.HIGH
        if score >= 70: return cls.MEDIUM
        if score >= 60: return cls.WATCH
        return cls.IGNORE

    def gte(self, other: "Priority") -> bool:
        _order = [Priority.IGNORE, Priority.WATCH, Priority.MEDIUM,
                  Priority.HIGH, Priority.ELITE]
        return _order.index(self) >= _order.index(other)


class RiskLevel(str, Enum):
    LOW    = "low"
    MEDIUM = "medium"
    HIGH   = "high"


class MarketState(str, Enum):
    BREAKOUT  = "breakout"
    BULL_TREND = "bull_trend"
    PULLBACK  = "pullback"
    RECOVERY  = "recovery"
    DOWNTREND = "downtrend"
    SIDEWAYS  = "sideways"


class OppType(str, Enum):
    MOMENTUM_TRADE  = "momentum_trade"
    CONTINUATION    = "continuation"
    ACCUMULATION    = "accumulation"
    RECOVERY_TRADE  = "recovery_trade"
    WATCHLIST       = "watchlist"
    AVOID           = "avoid"


class ExitReason(str, Enum):
    TAKE_PROFIT     = "TAKE_PROFIT"
    STOP_LOSS       = "STOP_LOSS"
    MANUAL          = "MANUAL"
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER"


class PositionStatus(str, Enum):
    OPEN    = "OPEN"
    CLOSING = "CLOSING"
    CLOSED  = "CLOSED"


# ── Domain Objects ────────────────────────────────────────────────────────────

@dataclass
class Signal:
    id:               str
    coin:             str
    pair:             str
    market_state:     MarketState
    opportunity_type: OppType
    priority:         Priority
    risk_level:       RiskLevel
    score:            int          # 0–100
    confidence:       int          # 0–100
    coin_class:       Optional[str]  # "A"|"B"|"C"
    mtf_alignment:    bool
    generated_at:     datetime
    expires_at:       datetime
    source_bot:       str = "scanner_v1"
    raw_payload:      dict = field(default_factory=dict)

    @property
    def is_live(self) -> bool:
        from datetime import timezone
        return datetime.now(timezone.utc) < self.expires_at

    @property
    def is_actionable(self) -> bool:
        return self.is_live and self.priority in (
            Priority.ELITE, Priority.HIGH, Priority.MEDIUM
        )


@dataclass
class Position:
    id:              str
    bot:             BotName
    coin:            str
    pair:            str
    qty:             float
    entry_price:     float
    entry_time:      datetime
    mode:            BotMode
    status:          PositionStatus = PositionStatus.OPEN
    current_price:   Optional[float] = None
    unrealised_pnl:  Optional[float] = None
    stop_loss:       Optional[float] = None
    take_profit:     Optional[float] = None
    signal_id:       Optional[str]   = None
    closed_at:       Optional[datetime] = None
    exit_price:      Optional[float] = None
    exit_reason:     Optional[ExitReason] = None

    @property
    def deployed_capital(self) -> float:
        return self.qty * self.entry_price


@dataclass
class Trade:
    id:          str
    position_id: str
    bot:         BotName
    coin:        str
    pair:        str
    entry_price: float
    exit_price:  float
    qty:         float
    pnl:         float
    pnl_pct:     float
    entry_time:  datetime
    exit_time:   datetime
    exit_reason: ExitReason
    mode:        BotMode
    signal_id:   Optional[str] = None


@dataclass
class BotSnapshot:
    bot:             BotName
    mode:            BotMode
    status:          BotStatus
    cash_balance:    float
    deployed_capital: float
    open_positions:  int
    total_pnl:       float
    last_cycle_at:   Optional[datetime]
    health_score:    int           # 0–100
    captured_at:     datetime


@dataclass
class PortfolioSnapshot:
    total_aum:            float
    total_deployed:       float
    total_cash:           float
    total_unrealised_pnl: float
    total_realised_pnl:   float
    daily_pnl:            float
    capital_utilisation:  float    # percent
    positions_by_bot:     dict     # BotName → list[Position]
    captured_at:          datetime
