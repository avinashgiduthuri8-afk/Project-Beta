"""Indian Stock Market Clock (IST Session Guard & Trading Windows)."""

from __future__ import annotations

from datetime import datetime, time, timezone, timedelta
from typing import Optional
from core.enums import MarketSession

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None


class MarketClock:
    """Manages Indian Market (NSE/BSE) trading sessions in Asia/Kolkata timezone (UTC+5:30)."""

    def __init__(
        self,
        timezone_str: str = "Asia/Kolkata",
        pre_open_str: str = "09:00:00",
        market_open_str: str = "09:15:00",
        square_off_str: str = "15:15:00",
        market_close_str: str = "15:30:00",
    ):
        try:
            if ZoneInfo is not None:
                self.tz = ZoneInfo(timezone_str)
            else:
                self.tz = timezone(timedelta(hours=5, minutes=30), name="IST")
        except Exception:
            # Fallback for Windows environments without tzdata package
            self.tz = timezone(timedelta(hours=5, minutes=30), name="IST")

        self.pre_open_time = time.fromisoformat(pre_open_str)
        self.market_open_time = time.fromisoformat(market_open_str)
        self.square_off_time = time.fromisoformat(square_off_str)
        self.market_close_time = time.fromisoformat(market_close_str)

    def now_ist(self) -> datetime:
        """Current datetime in Indian Standard Time (IST)."""
        return datetime.now(self.tz)

    def is_trading_day(self, dt: Optional[datetime] = None) -> bool:
        """Check if today is Monday - Friday (excluding weekends)."""
        check_dt = dt or self.now_ist()
        # 0 = Monday, 4 = Friday, 5 = Saturday, 6 = Sunday
        return check_dt.weekday() < 5

    def get_current_session(self, dt: Optional[datetime] = None) -> MarketSession:
        """Determine current trading session phase."""
        check_dt = dt or self.now_ist()
        if not self.is_trading_day(check_dt):
            return MarketSession.CLOSED

        current_time = check_dt.time()

        if current_time < self.pre_open_time:
            return MarketSession.CLOSED
        elif self.pre_open_time <= current_time < self.market_open_time:
            return MarketSession.PRE_OPEN
        elif self.market_open_time <= current_time < self.square_off_time:
            return MarketSession.NORMAL
        elif self.square_off_time <= current_time < self.market_close_time:
            return MarketSession.SQUARE_OFF_WINDOW
        else:
            return MarketSession.POST_CLOSE

    def is_normal_trading_active(self, dt: Optional[datetime] = None) -> bool:
        """Check if trading is allowed for regular new positions (09:15 - 15:15 IST)."""
        return self.get_current_session(dt) == MarketSession.NORMAL

    def is_auto_square_off_time(self, dt: Optional[datetime] = None) -> bool:
        """Check if intraday positions must be squared off (>= 15:15 IST)."""
        session = self.get_current_session(dt)
        return session in (MarketSession.SQUARE_OFF_WINDOW, MarketSession.POST_CLOSE)
