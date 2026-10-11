"""Small canonical domain model for Project Beta v1."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


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


class Priority(str, Enum):
    ELITE = "Elite"
    HIGH = "High"
    MEDIUM = "Medium"
    WATCH = "Watch"
    IGNORE = "Ignore"

    def gte(self, other: "Priority") -> bool:
        order = [self.IGNORE, self.WATCH, self.MEDIUM, self.HIGH, self.ELITE]
        return order.index(self) >= order.index(other)


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


@dataclass(slots=True)
class Signal:
    id: str
    coin: str
    pair: str
    market_state: MarketState
    opportunity_type: OppType
    priority: Priority
    risk_level: RiskLevel
    score: int
    confidence: int
    coin_class: str | None
    mtf_alignment: bool
    generated_at: datetime
    expires_at: datetime
    mode: BotMode = BotMode.PAPER
    source_bot: str = "scanner"
    raw_payload: dict = field(default_factory=dict)
    confluence_breakdown: dict | None = field(default_factory=dict)
    symbol: str | None = None
    exchange: str = "NSE"
    security_id: str | None = None

    @property
    def is_live(self) -> bool:
        return datetime.now(self.expires_at.tzinfo) < self.expires_at
