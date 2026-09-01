"""Abstract ExecutionHandler base class."""

from __future__ import annotations

from abc import ABC, abstractmethod
from sector_quant.events.events import OrderEvent, FillEvent
from sector_quant.events.queue import EventQueue


class ExecutionHandler(ABC):
    """Abstract base class for all order execution handlers (simulated or live broker)."""

    def __init__(self, events_queue: EventQueue):
        self.events_queue = events_queue

    @abstractmethod
    def execute_order(self, event: OrderEvent) -> None:
        """Processes an OrderEvent and emits a FillEvent upon completion."""
        raise NotImplementedError

