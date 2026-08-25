"""Domain Enums for Indian Equities & Derivatives Trading."""

from enum import Enum


class Exchange(str, Enum):
    NSE = "NSE"      # National Stock Exchange (Cash)
    BSE = "BSE"      # Bombay Stock Exchange (Cash)
    NFO = "NFO"      # NSE Futures & Options
    BFO = "BFO"      # BSE Futures & Options
    CDS = "CDS"      # Currency Derivatives
    MCX = "MCX"      # Multi Commodity Exchange


class ProductType(str, Enum):
    MIS = "MIS"      # Margin Intraday Square-off (Day trade)
    CNC = "CNC"      # Cash and Carry (Equity delivery)
    NRML = "NRML"    # Normal (Derivatives carryforward)


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    SL = "SL"          # Stop-Loss Limit
    SL_M = "SL-M"      # Stop-Loss Market (restricted by NSE on options, but standard enum)


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
    PRE_OPEN = "PRE_OPEN"          # 09:00 - 09:08 IST
    NORMAL = "NORMAL"              # 09:15 - 15:30 IST
    SQUARE_OFF_WINDOW = "SQUARE_OFF_WINDOW"  # 15:15 - 15:30 IST
    POST_CLOSE = "POST_CLOSE"      # 15:30 - 16:00 IST
