"""Abstract DataHandler base class."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, List, Any, Optional
import numpy as np

from sector_quant.events.events import MarketEvent
from sector_quant.events.queue import EventQueue


class DataHandler(ABC):
    """Abstract base class providing an interface for all data handlers (historical or live)."""

    def __init__(self, events_queue: EventQueue, symbol_list: List[str]):
        self.events_queue = events_queue
        self.symbol_list = [s.upper() for s in symbol_list]
        self.continue_backtest = True

    @abstractmethod
    def get_latest_bar(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Returns the last bar updated."""
        raise NotImplementedError

    @abstractmethod
    def get_latest_bars(self, symbol: str, N: int = 1) -> List[Dict[str, Any]]:
        """Returns the last N bars updated."""
        raise NotImplementedError

    @abstractmethod
    def get_latest_bar_datetime(self, symbol: str) -> Optional[datetime]:
        """Returns a datetime object for the last bar."""
        raise NotImplementedError

    @abstractmethod
    def get_latest_bar_value(self, symbol: str, val_type: str = "close") -> Optional[float]:
        """Returns one of the Open, High, Low, Close, Volume or Adj Close values from the last bar."""
        raise NotImplementedError

    @abstractmethod
    def get_latest_bars_values(self, symbol: str, val_type: str = "close", N: int = 1) -> np.ndarray:
        """Returns the last N bar values (e.g. close prices) as a numpy array."""
        raise NotImplementedError

    @abstractmethod
    def update_bars(self) -> bool:
        """Pushes the latest bar to the latest_symbol_data structure for all symbols.
        Returns False when there are no more bars to process.
        """
        raise NotImplementedError

