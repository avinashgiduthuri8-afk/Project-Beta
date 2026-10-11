"""Abstract market-data interface for event-driven simulations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from queue import Queue
from typing import Any


class DataHandler(ABC):
    """Drip-feed interface that prevents consumers from looking ahead."""

    def __init__(self, events_queue: Queue) -> None:
        self.events = events_queue
        self.continue_backtest = True

    @abstractmethod
    def get_latest_bar(self, symbol: str) -> dict[str, Any] | None:
        raise NotImplementedError

    @abstractmethod
    def get_latest_bars(self, symbol: str, N: int = 1) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def get_latest_bar_datetime(self, symbol: str) -> datetime | None:
        raise NotImplementedError

    @abstractmethod
    def get_latest_bar_value(self, symbol: str, val_type: str) -> float | None:
        raise NotImplementedError

    @abstractmethod
    def get_latest_bars_values(
        self, symbol: str, val_type: str, N: int = 1
    ) -> list[float]:
        raise NotImplementedError

    @abstractmethod
    def update_bars(self) -> None:
        raise NotImplementedError
