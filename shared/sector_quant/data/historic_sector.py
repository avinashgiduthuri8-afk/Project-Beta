"""Synchronized multi-symbol historical stock-data handler."""

from __future__ import annotations

from datetime import datetime
from queue import Queue
from typing import Any

from sector_quant.data.base import DataHandler
from sector_quant.events import MarketEvent


class HistoricSectorDataHandler(DataHandler):
    """Drip-feed historical OHLCV bars on a shared chronological timeline."""

    def __init__(
        self,
        events_queue: Queue,
        symbol_list: list[str],
        data_feed: dict[str, list[dict[str, Any]]],
    ) -> None:
        super().__init__(events_queue)
        self.symbol_list = [symbol.upper() for symbol in symbol_list]
        self._raw_data = {
            symbol: sorted(data_feed.get(symbol, []), key=lambda bar: bar["price_date"])
            for symbol in self.symbol_list
        }
        self.latest_symbol_data: dict[str, list[dict[str, Any]]] = {
            symbol: [] for symbol in self.symbol_list
        }
        self._timeline = sorted(
            {
                bar["price_date"]
                for bars in self._raw_data.values()
                for bar in bars
            }
        )
        self._timeline_idx = 0
        self._symbol_indices = {symbol: 0 for symbol in self.symbol_list}

    def get_latest_bar(self, symbol: str) -> dict[str, Any] | None:
        bars = self.latest_symbol_data.get(symbol.upper(), [])
        return bars[-1] if bars else None

    def get_latest_bars(self, symbol: str, N: int = 1) -> list[dict[str, Any]]:
        bars = self.latest_symbol_data.get(symbol.upper(), [])
        return bars[-N:] if bars else []

    def get_latest_bar_datetime(self, symbol: str) -> datetime | None:
        bar = self.get_latest_bar(symbol)
        if not bar:
            return None
        try:
            return datetime.fromisoformat(str(bar.get("price_date", "")))
        except (TypeError, ValueError):
            return None

    def get_latest_bar_value(self, symbol: str, val_type: str) -> float | None:
        bar = self.get_latest_bar(symbol)
        return float(bar.get(val_type.lower(), 0.0)) if bar else None

    def get_latest_bars_values(
        self, symbol: str, val_type: str, N: int = 1
    ) -> list[float]:
        return [
            float(bar.get(val_type.lower(), 0.0))
            for bar in self.get_latest_bars(symbol, N)
        ]

    def update_bars(self) -> None:
        if self._timeline_idx >= len(self._timeline):
            self.continue_backtest = False
            return

        current_time = self._timeline[self._timeline_idx]
        has_new_bar = False
        for symbol in self.symbol_list:
            index = self._symbol_indices[symbol]
            bars = self._raw_data[symbol]
            if index < len(bars) and bars[index]["price_date"] == current_time:
                self.latest_symbol_data[symbol].append(bars[index])
                self._symbol_indices[symbol] += 1
                has_new_bar = True

        self._timeline_idx += 1
        if has_new_bar:
            self.events.put(MarketEvent())
        elif self._timeline_idx >= len(self._timeline):
            self.continue_backtest = False
