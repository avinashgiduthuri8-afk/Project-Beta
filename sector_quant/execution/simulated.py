"""Simulated Execution Handler with realistic commissions and slippage modeling."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, Any
import numpy as np

from sector_quant.execution.base import ExecutionHandler
from sector_quant.data.base import DataHandler
from sector_quant.events.events import OrderEvent, FillEvent, OrderDirection
from sector_quant.events.queue import EventQueue

logger = logging.getLogger(__name__)


class CommissionScheme(str, Enum):
    PER_SHARE = "PER_SHARE"          # e.g., $0.005 per share, min $1.00 (Interactive Brokers style)
    PERCENTAGE = "PERCENTAGE"        # e.g., 0.03% (NSE/BSE Indian brokerage style)
    ZERO = "ZERO"


@dataclass
class ExecutionCostConfig:
    commission_scheme: CommissionScheme = CommissionScheme.PER_SHARE
    per_share_rate: float = 0.005     # $0.005 / share
    min_commission: float = 1.0       # Minimum $1.00 per order
    percentage_rate: float = 0.0003   # 3 basis points (0.03%)
    slippage_bps: float = 5.0         # 5 basis points (0.05%)


class SimulatedExecutionHandler(ExecutionHandler):
    """Simulates realistic fills for OrderEvents with commissions and slippage."""

    def __init__(
        self,
        events_queue: EventQueue,
        bars: DataHandler,
        cost_config: Optional[ExecutionCostConfig] = None,
        exchange: str = "SIMULATED",
    ):
        super().__init__(events_queue=events_queue)
        self.bars = bars
        self.config = cost_config or ExecutionCostConfig()
        self.exchange = exchange
        self.order_counter = 0

    def calculate_commission(self, quantity: int, fill_price: float) -> float:
        """Computes brokerage commission and exchange fees."""
        if self.config.commission_scheme == CommissionScheme.ZERO:
            return 0.0

        if self.config.commission_scheme == CommissionScheme.PER_SHARE:
            comm = quantity * self.config.per_share_rate
            return max(self.config.min_commission, comm)

        if self.config.commission_scheme == CommissionScheme.PERCENTAGE:
            order_val = quantity * fill_price
            return max(self.config.min_commission, order_val * self.config.percentage_rate)

        return 0.0

    def calculate_slippage(self, base_price: float, direction: OrderDirection) -> tuple[float, float]:
        """Calculates slippage dollar penalty and adjusted execution fill price."""
        slip_pct = self.config.slippage_bps / 10000.0  # 1 bp = 0.0001
        if direction == OrderDirection.BUY:
            fill_price = base_price * (1.0 + slip_pct)
            slippage_dollars = fill_price - base_price
        else:
            fill_price = base_price * (1.0 - slip_pct)
            slippage_dollars = base_price - fill_price

        return fill_price, slippage_dollars

    def execute_order(self, event: OrderEvent) -> None:
        """Processes OrderEvent and places a FillEvent on the queue."""
        self.order_counter += 1
        symbol = event.symbol.upper()
        timeindex = event.datetime or self.bars.get_latest_bar_datetime(symbol) or datetime.now()

        # Retrieve latest execution benchmark price
        base_price = event.price
        if base_price is None or base_price <= 0:
            base_price = self.bars.get_latest_bar_value(symbol, "close")

        if base_price is None or base_price <= 0:
            logger.error(f"Cannot execute order for {symbol}: invalid price {base_price}")
            return

        fill_price, slippage_dollars = self.calculate_slippage(base_price, event.direction)
        commission = self.calculate_commission(event.quantity, fill_price)
        fill_cost = event.quantity * fill_price

        fill = FillEvent(
            timeindex=timeindex,
            symbol=symbol,
            exchange=self.exchange,
            quantity=event.quantity,
            direction=event.direction,
            fill_price=fill_price,
            fill_cost=fill_cost,
            commission=commission,
            slippage=slippage_dollars * event.quantity,
            order_id=f"SIM_ORD_{self.order_counter:06d}",
            strategy_id=event.strategy_id,
            sector=event.sector,
            meta=event.meta,
        )

        self.events_queue.put(fill)

