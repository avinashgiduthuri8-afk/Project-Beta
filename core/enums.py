"""Core Enums for Indian Stock Trading Platform (Project-Beta)."""

from enum import Enum


class Exchange(str, Enum):
    NSE = "NSE"
    BSE = "BSE"
    NFO = "NFO"
    BFO = "BFO"


class ProductType(str, Enum):
    MIS = "MIS"      # Intraday Margin Square-off
    CNC = "CNC"      # Cash and Carry (Delivery)
    NRML = "NRML"    # Normal (Derivatives Carryforward)


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    SL = "SL"
    SL_M = "SL-M"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    TRIGGER_PENDING = "TRIGGER_PENDING"
    COMPLETE = "COMPLETE"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class TimeInForce(str, Enum):
    DAY = "DAY"
    IOC = "IOC"      # Immediate or Cancel


class MarketSession(str, Enum):
    CLOSED = "CLOSED"
    PRE_OPEN = "PRE_OPEN"                    # 09:00 - 09:08 IST
    PRE_OPEN_MATCH = "PRE_OPEN_MATCH"        # 09:08 - 09:15 IST
    NORMAL = "NORMAL"                        # 09:15 - 15:15 IST
    SQUARE_OFF_WINDOW = "SQUARE_OFF_WINDOW"  # 15:15 - 15:30 IST
    POST_CLOSE = "POST_CLOSE"                # 15:30 - 16:00 IST


class MarketRegime(str, Enum):
    BULLISH_TRENDING = "BULLISH_TRENDING"
    BEARISH_TRENDING = "BEARISH_TRENDING"
    HIGH_VOLATILITY_EXPANSION = "HIGH_VOLATILITY_EXPANSION"
    LOW_VOLATILITY_CHOP = "LOW_VOLATILITY_CHOP"
    NEUTRAL = "NEUTRAL"


class SetupType(str, Enum):
    MINERVINI_VCP = "MINERVINI_VCP"
    POCKET_PIVOT = "POCKET_PIVOT"
    NR7_SQUEEZE = "NR7_SQUEEZE"
    HIGH_DELIVERY_BREAKOUT = "HIGH_DELIVERY_BREAKOUT"
    VWAP_MOMENTUM = "VWAP_MOMENTUM"
    MEAN_REVERSION_FADE = "MEAN_REVERSION_FADE"


class Timeframe(str, Enum):
    M15 = "15m"
    H1 = "1h"
    D1 = "1d"


class AISignalDecision(str, Enum):
    CONFIRMED = "CONFIRMED"
    CHALLENGED = "CHALLENGED"
    REJECTED = "REJECTED"
    NEUTRAL = "NEUTRAL"
