"""Abstract Strategy base class."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List
from sector_quant.data.base import DataHandler
from sector_quant.events.events import MarketEvent, SignalEvent, SignalType
from sector_quant.events.queue import EventQueue


class Strategy(ABC):
    """Abstract base class providing an interface for all sector quantitative strategies."""

    def __init__(self, bars: DataHandler, events_queue: EventQueue, strategy_id: str = "BASE_STRAT"):
        self.bars = bars
        self.events_queue = events_queue
        self.strategy_id = strategy_id

    @abstractmethod
    def calculate_signals(self, event: MarketEvent) -> None:
        """Evaluates latest market bars and emits SignalEvent objects to the queue."""
        raise NotImplementedError

    def emit_signal(
        self,
        symbol: str,
        signal_type: SignalType,
        datetime: Any,
        strength: float = 1.0,
        target_price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> SignalEvent:
        """Helper to create and push a SignalEvent onto the queue."""
        sig = SignalEvent(
            strategy_id=self.strategy_id,
            symbol=symbol.upper(),
            datetime=datetime,
            signal_type=signal_type,
            strength=strength,
            target_price=target_price,
            stop_loss=stop_loss,
            meta=meta or {},
        )
        self.events_queue.put(sig)
        return sig

