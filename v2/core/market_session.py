"""Market Session & Trading Hours Guard for Equities (NSE/BSE & NYSE/NASDAQ)."""

from __future__ import annotations

import logging
from datetime import datetime, time, date
from enum import Enum
from typing import Dict, Any, Optional, List
import pytz

logger = logging.getLogger(__name__)

# Timezones
IST_TZ = pytz.timezone("Asia/Kolkata")
US_EASTERN_TZ = pytz.timezone("America/New_York")

# Standard NSE/BSE Market Holidays 2026 (Sample static calendar)
NSE_HOLIDAYS_2026 = {
    date(2026, 1, 26),  # Republic Day
    date(2026, 3, 17),  # Holi
    date(2026, 4, 3),   # Good Friday
    date(2026, 4, 14),  # Ambedkar Jayanti
    date(2026, 5, 1),   # Maharashtra Day
    date(2026, 8, 15),  # Independence Day
    date(2026, 10, 2),  # Gandhi Jayanti
    date(2026, 10, 20), # Dussehra
    date(2026, 11, 8),  # Diwali Laxmi Pujan
    date(2026, 12, 25), # Christmas
}

# Standard US Holidays 2026
US_HOLIDAYS_2026 = {
    date(2026, 1, 1),   # New Year's Day
    date(2026, 1, 19),  # MLK Day
    date(2026, 2, 16),  # Presidents Day
    date(2026, 4, 3),   # Good Friday
    date(2026, 5, 25),  # Memorial Day
    date(2026, 6, 19),  # Juneteenth
    date(2026, 7, 3),   # Independence Day (Observed)
    date(2026, 9, 7),   # Labor Day
    date(2026, 11, 26), # Thanksgiving
    date(2026, 12, 25), # Christmas
}


class MarketSession(str, Enum):
    CLOSED = "CLOSED"
    PRE_MARKET = "PRE_MARKET"
    REGULAR = "REGULAR"
    SQUARE_OFF_ONLY = "SQUARE_OFF_ONLY"
    POST_MARKET = "POST_MARKET"


class MarketSessionGuard:
    """Enforces market timing rules, exchange trading hours, and intraday square-off timing."""

    def __init__(self, exchange: str = "NSE"):
        self.exchange = exchange.upper()

    def get_current_time(self) -> datetime:
        """Returns localized current datetime based on target exchange."""
        if self.exchange in ["NSE", "BSE", "NFO", "BFO"]:
            return datetime.now(IST_TZ)
        else:
            return datetime.now(US_EASTERN_TZ)

    def is_holiday(self, check_date: Optional[date] = None) -> bool:
        """Checks if a given date is an official exchange holiday."""
        d = check_date or self.get_current_time().date()
        if self.exchange in ["NSE", "BSE", "NFO", "BFO"]:
            return d in NSE_HOLIDAYS_2026
        else:
            return d in US_HOLIDAYS_2026

    def is_weekend(self, check_dt: Optional[datetime] = None) -> bool:
        """Checks if current/given timestamp falls on a weekend (Saturday=5, Sunday=6)."""
        dt = check_dt or self.get_current_time()
        return dt.weekday() >= 5

    def get_session_status(self, check_dt: Optional[datetime] = None) -> MarketSession:
        """Determines active market session status for the current exchange."""
        dt = check_dt or self.get_current_time()

        if self.is_weekend(dt) or self.is_holiday(dt.date()):
            return MarketSession.CLOSED

        t = dt.time()

        if self.exchange in ["NSE", "BSE", "NFO", "BFO"]:
            # NSE Market Hours: Pre-open (09:00-09:15), Regular (09:15-15:15), Square-off (15:15-15:30)
            if time(9, 0) <= t < time(9, 15):
                return MarketSession.PRE_MARKET
            elif time(9, 15) <= t < time(15, 15):
                return MarketSession.REGULAR
            elif time(15, 15) <= t < time(15, 30):
                return MarketSession.SQUARE_OFF_ONLY
            else:
                return MarketSession.CLOSED
        else:
            # US Market Hours: Pre-market (04:00-09:30), Regular (09:30-15:45), Square-off (15:45-16:00)
            if time(4, 0) <= t < time(9, 30):
                return MarketSession.PRE_MARKET
            elif time(9, 30) <= t < time(15, 45):
                return MarketSession.REGULAR
            elif time(15, 45) <= t < time(16, 0):
                return MarketSession.SQUARE_OFF_ONLY
            else:
                return MarketSession.CLOSED

    def is_market_open(self) -> bool:
        """Returns True if regular trading session is active."""
        status = self.get_session_status()
        return status in [MarketSession.REGULAR, MarketSession.SQUARE_OFF_ONLY]

    def is_square_off_time(self) -> bool:
        """Returns True if intraday MIS positions should be squared off before market close."""
        status = self.get_session_status()
        return status == MarketSession.SQUARE_OFF_ONLY

