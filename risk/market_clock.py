"""
Indian Market Session & Market Clock Guard (IST Timezone).
Tracks NSE/BSE trading sessions: Pre-open, Normal trading, Auto-square-off, and Market close.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Optional
import pytz
from core.enums import MarketSession

IST = pytz.timezone("Asia/Kolkata")


class MarketClock:
    """
    Guards execution against out-of-hours trades and triggers intraday square-off at 15:15 IST.
    """

    def __init__(
        self,
        pre_market_start: str = "09:00:00",
        market_open: str = "09:15:00",
        auto_square_off: str = "15:15:00",
        market_close: str = "15:30:00",
    ) -> None:
        self.t_pre_market = self._parse_time(pre_market_start)
        self.t_open = self._parse_time(market_open)
        self.t_square_off = self._parse_time(auto_square_off)
        self.t_close = self._parse_time(market_close)

    @staticmethod
    def _parse_time(t_str: str) -> time:
        parts = [int(p) for p in t_str.split(":")]
        return time(hour=parts[0], minute=parts[1], second=parts[2] if len(parts) > 2 else 0)

    @staticmethod
    def get_ist_now() -> datetime:
        """Get current datetime in Indian Standard Time (IST)."""
        return datetime.now(IST)

    def get_session(self, dt: Optional[datetime] = None) -> MarketSession:
        """Determine current market session state in IST."""
        now = dt or self.get_ist_now()
        if now.tzinfo is None:
            now = IST.localize(now)
        else:
            now = now.astimezone(IST)

        # Check weekend
        if now.weekday() in (5, 6):  # Saturday=5, Sunday=6
            return MarketSession.WEEKEND

        curr_time = now.time()

        if curr_time < self.t_pre_market:
            return MarketSession.CLOSED
        elif self.t_pre_market <= curr_time < time(9, 8):
            return MarketSession.PRE_OPEN
        elif time(9, 8) <= curr_time < self.t_open:
            return MarketSession.PRE_OPEN_BUFFER
        elif self.t_open <= curr_time < self.t_square_off:
            return MarketSession.TRADING
        elif self.t_square_off <= curr_time < self.t_close:
            return MarketSession.AUTO_SQUARE_OFF
        else:
            return MarketSession.POST_CLOSE

    def is_trading_allowed(self, dt: Optional[datetime] = None) -> bool:
        """Returns True only during normal trading hours (09:15 - 15:15 IST)."""
        return self.get_session(dt) == MarketSession.TRADING

    def is_square_off_time(self, dt: Optional[datetime] = None) -> bool:
        """Returns True if at or past auto-square-off threshold (15:15 IST)."""
        session = self.get_session(dt)
        return session in (MarketSession.AUTO_SQUARE_OFF, MarketSession.POST_CLOSE)
