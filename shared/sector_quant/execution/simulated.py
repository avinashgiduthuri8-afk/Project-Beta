"""
sector_quant.execution.simulated — Simulated broker execution with realistic slippage & commissions.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from queue import Queue

from sector_quant.data.base import DataHandler
from sector_quant.events import Event, FillEvent, OrderEvent


class ExecutionHandler(ABC):
    """Abstract execution handler."""

    @abstractmethod
    def execute_order(self, event: Event) -> None:
        raise NotImplementedError


class SimulatedExecutionHandler(ExecutionHandler):
    """
    Simulates instantaneous exchange filling with configurable
    linear slippage and per-share / percentage commission modeling.
    """

    def __init__(
        self,
        events_queue: Queue,
        bars: DataHandler | None = None,
        commission_pct: float = 0.0005,  # 5 bps
        slippage_pct: float = 0.0005,  # 5 bps
        min_commission: float = 1.0,  # Minimum INR 1.00 per order
    ) -> None:
        self.events = events_queue
        self.bars = bars
        self.commission_pct = commission_pct
        self.slippage_pct = slippage_pct
        self.min_commission = min_commission

    def execute_order(self, event: Event) -> None:
        if event.type != "ORDER":
            return

        order: OrderEvent = event  # type: ignore
        sym = order.symbol.upper()
        base_price = order.price

        if base_price is None and self.bars is not None:
            base_price = self.bars.get_latest_bar_value(sym, "close")

        if base_price is None or base_price <= 0:
            return

        # 1. Apply directional slippage
        if order.direction.upper() == "BUY":
            fill_price = base_price * (1.0 + self.slippage_pct)
        else:
            fill_price = base_price * (1.0 - self.slippage_pct)

        fill_cost = fill_price * order.quantity
        commission = max(self.min_commission, fill_cost * self.commission_pct)
        slippage_drag = abs(fill_price - base_price) * order.quantity

        dt = datetime.utcnow()
        if self.bars is not None:
            dt = self.bars.get_latest_bar_datetime(sym) or dt

        fill = FillEvent(
            timeindex=dt,
            symbol=sym,
            exchange="NSE",
            quantity=order.quantity,
            direction=order.direction.upper(),
            fill_cost=fill_cost,
            commission=commission,
            slippage=slippage_drag,
        )

        self.events.put(fill)
