"""Indian Market Session & Liquidity Filter Layer (Asia/Kolkata IST)."""

from __future__ import annotations

import logging
from datetime import datetime, time, timezone, timedelta, date
from typing import Optional, List, Set
from core.enums import MarketSession

logger = logging.getLogger(__name__)

# Standard NSE Trading Holidays (Example Calendar for validation)
NSE_HOLIDAYS_2026: Set[str] = {
    "2026-01-26",  # Republic Day
    "2026-03-06",  # Maha Shivratri
    "2026-03-25",  # Holi
    "2026-04-03",  # Good Friday
    "2026-04-14",  # Dr. Baba Saheb Ambedkar Jayanti
    "2026-05-01",  # Maharashtra Day
    "2026-08-15",  # Independence Day
    "2026-10-02",  # Mahatma Gandhi Jayanti
    "2026-10-20",  # Dussehra
    "2026-11-08",  # Diwali Laxmi Pujan
    "2026-12-25",  # Christmas
}


class IndianMarketSession:
    """Manages IST trading sessions, holiday calendars, and liquidity eligibility rules."""

    def __init__(
        self,
        pre_open_time: str = "09:00:00",
        market_open_time: str = "09:15:00",
        square_off_time: str = "15:15:00",
        market_close_time: str = "15:30:00",
        min_daily_turnover_cr: float = 5.0,  # Min ₹5 Crore daily turnover
        min_price: float = 50.0,             # Min ₹50 share price (avoid penny stocks)
        min_median_volume: int = 100000,     # Min 100k daily volume
    ):
        self.tz = timezone(timedelta(hours=5, minutes=30), name="IST")
        self.pre_open = time.fromisoformat(pre_open_time)
        self.market_open = time.fromisoformat(market_open_time)
        self.square_off = time.fromisoformat(square_off_time)
        self.market_close = time.fromisoformat(market_close_time)

        self.min_daily_turnover_cr = min_daily_turnover_cr
        self.min_price = min_price
        self.min_median_volume = min_median_volume

    def now_ist(self) -> datetime:
        """Current timestamp in IST timezone."""
        return datetime.now(self.tz)

    def is_holiday(self, check_date: Optional[date] = None) -> bool:
        """Check if date is an NSE trading holiday."""
        d = check_date or self.now_ist().date()
        return d.isoformat() in NSE_HOLIDAYS_2026

    def is_trading_day(self, dt: Optional[datetime] = None) -> bool:
        """Check if date is a weekday and not an NSE holiday."""
        check_dt = dt or self.now_ist()
        if check_dt.weekday() >= 5:  # Saturday or Sunday
            return False
        if self.is_holiday(check_dt.date()):
            return False
        return True

    def get_current_session(self, dt: Optional[datetime] = None) -> MarketSession:
        """Evaluate the active Indian market session."""
        check_dt = dt or self.now_ist()
        if not self.is_trading_day(check_dt):
            return MarketSession.CLOSED

        t = check_dt.time()
        if t < self.pre_open:
            return MarketSession.CLOSED
        elif self.pre_open <= t < time(9, 8, 0):
            return MarketSession.PRE_OPEN
        elif time(9, 8, 0) <= t < self.market_open:
            return MarketSession.PRE_OPEN_MATCH
        elif self.market_open <= t < self.square_off:
            return MarketSession.NORMAL
        elif self.square_off <= t < self.market_close:
            return MarketSession.SQUARE_OFF_WINDOW
        else:
            return MarketSession.POST_CLOSE

    def is_scanning_and_trading_allowed(self, dt: Optional[datetime] = None) -> bool:
        """Scanning and new trade triggers permitted only during normal market hours (09:15 - 15:15 IST)."""
        return self.get_current_session(dt) == MarketSession.NORMAL

    def is_auto_square_off_time(self, dt: Optional[datetime] = None) -> bool:
        """Check if intraday MIS positions must be closed (>= 15:15 IST)."""
        return self.get_current_session(dt) in (MarketSession.SQUARE_OFF_WINDOW, MarketSession.POST_CLOSE)

    def validate_stock_liquidity(self, price: float, volume: int, avg_delivery_pct: float = 0.0) -> tuple[bool, str]:
        """Apply universe, price, and turnover eligibility filters before scanner compute."""
        if price < self.min_price:
            return False, f"Price ₹{price:.2f} < Min ₹{self.min_price:.2f} (Penny stock filter)"

        turnover_cr = (price * volume) / 10000000.0  # 1 Crore = 10,000,000 INR
        if turnover_cr < self.min_daily_turnover_cr and volume < self.min_median_volume:
            return False, f"Daily turnover ₹{turnover_cr:.2f}Cr < Min ₹{self.min_daily_turnover_cr}Cr"

        return True, "Eligible for High-Quality Scanning"
