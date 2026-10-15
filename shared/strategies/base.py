"""
Base interfaces and data structures for the Project Beta Strategy Suite.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import pandas as pd

from market.symbols import StockSymbol


@dataclass
class StrategySignal:
    strategy_name: str
    symbol: StockSymbol
    entry_price: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    score: int = 85
    direction: str = "LONG"
    timeframe: str = "15m"
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy_name,
            "symbol": self.symbol.trading_symbol,
            "exchange": self.symbol.exchange,
            "canonical_symbol": self.symbol.canonical,
            "price": str(self.entry_price),
            "stop_loss": str(self.stop_loss),
            "take_profit": str(self.take_profit),
            "score": self.score,
            "direction": self.direction,
            "timeframe": self.timeframe,
            "metadata": self.metadata,
        }


class BaseStockStrategy(ABC):
    """Abstract base class for all Indian cash equity strategies."""

    def __init__(self, name: str) -> None:
        self.name = name

    @abstractmethod
    def evaluate(self, df: pd.DataFrame, symbol: StockSymbol) -> StrategySignal | None:
        """
        Evaluate technical setup on the latest bar of an enriched DataFrame.
        Returns a StrategySignal if entry conditions are triggered, else None.
        Must strictly avoid lookahead bias by basing triggers on closed candle bars.
        """
