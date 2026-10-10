"""
Base Strategy Interface for Backtesting Suite.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass
class BacktestTradeSignal:
    strategy_name: str
    pair: str
    timeframe: str
    bar_index: int
    entry_price: float
    stop_loss_price: float
    take_profit_price: float
    direction: str = "LONG"
    metadata: dict[str, Any] = None

    def __post_init__(self) -> None:
        if self.metadata is None:
            self.metadata = {}


class BaseStrategy(ABC):
    """Abstract Base Class for all 10 candidate strategies."""

    def __init__(self, name: str) -> None:
        self.name = name

    @abstractmethod
    def generate_signals(
        self,
        df: pd.DataFrame,
        pair: str = "BTC/USDT",
        timeframe: str = "1H",
    ) -> list[BacktestTradeSignal]:
        """
        Scans OHLCV dataframe and generates trigger signals.
        Must avoid lookahead bias: signals trigger at bar close, execution at next bar open.
        """
