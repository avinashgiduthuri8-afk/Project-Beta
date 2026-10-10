"""Indian cash-market session guard used by scanner and execution layers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class MarketSession:
    exchange: str = "NSE"
    opens_at: time = time(9, 15)
    closes_at: time = time(15, 30)
    pre_open_at: time = time(9, 0)

    def is_open(
        self,
        now: datetime | None = None,
        holidays: set[date] | None = None,
    ) -> bool:
        current = (now or datetime.now(IST)).astimezone(IST)
        if current.weekday() >= 5 or current.date() in (holidays or set()):
            return False
        return self.opens_at <= current.time().replace(tzinfo=None) <= self.closes_at

    def is_pre_open(self, now: datetime | None = None) -> bool:
        current = (now or datetime.now(IST)).astimezone(IST)
        if current.weekday() >= 5:
            return False
        local_time = current.time().replace(tzinfo=None)
        return self.pre_open_at <= local_time < self.opens_at


NSE_SESSION = MarketSession("NSE")
BSE_SESSION = MarketSession("BSE")


def is_stock_market_open(now: datetime | None = None) -> bool:
    return NSE_SESSION.is_open(now)


def is_pre_market(now: datetime | None = None) -> bool:
    return NSE_SESSION.is_pre_open(now)
