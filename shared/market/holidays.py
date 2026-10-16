"""Fail-closed exchange holiday coverage."""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path


class HolidayCalendar:
    def __init__(self, holidays: dict[str, set[date]]) -> None:
        self.holidays = {key.upper(): set(values) for key, values in holidays.items()}
        self.covered_years = {
            key: {value.year for value in values} for key, values in self.holidays.items()
        }

    @classmethod
    def from_json(cls, path: str | Path) -> "HolidayCalendar":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls({
            exchange: {date.fromisoformat(value) for value in values}
            for exchange, values in payload.items()
        })

    def is_session_day(self, exchange: str, trading_date: date) -> bool:
        exchange = exchange.upper()
        if trading_date.weekday() >= 5:
            return False
        if trading_date.year not in self.covered_years.get(exchange, set()):
            return False
        return trading_date not in self.holidays.get(exchange, set())
