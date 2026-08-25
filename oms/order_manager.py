"""
Order Management System (OMS) & Order State Machine.
Tracks open, complete, and cancelled orders, with state transition hooks.
"""

from __future__ import annotations

import logging
from typing import Callable, Dict, List, Optional
from config.config_loader import BotSettings
from core.enums import OrderStatus
from core.interfaces import BaseBroker
from core.models import Order, OrderRequest
from oms.execution_router import ExecutionRouter

logger = logging.getLogger(__name__)


class OrderManager:
    """
    Maintains in-memory state of orders and coordinates order modifications/cancellations.
    """

    def __init__(
        self,
        broker: BaseBroker,
        settings: BotSettings,
        on_order_update: Optional[Callable[[Order], None]] = None,
    ) -> None:
        self.broker = broker
        self.settings = settings
        self.router = ExecutionRouter(broker=broker, settings=settings)
        self.on_order_update = on_order_update
        self._orders: Dict[str, Order] = {}

    def submit_order(
        self,
        request: OrderRequest,
        lot_size: int = 1,
        tick_size: float = 0.05,
    ) -> Order:
        """Submit a new order via the ExecutionRouter."""
        order = self.router.route_order(request, lot_size=lot_size, tick_size=tick_size)
        self._orders[order.order_id] = order

        if self.on_order_update:
            self.on_order_update(order)

        return order

    def update_order_state(self, updated_order: Order) -> None:
        """Update internal state when a webhook or order-book sync detects changes."""
        prev_order = self._orders.get(updated_order.order_id)
        self._orders[updated_order.order_id] = updated_order

        if prev_order and prev_order.status != updated_order.status:
            logger.info(
                f"Order {updated_order.order_id} ({updated_order.symbol}) transitioned: "
                f"{prev_order.status.value} -> {updated_order.status.value}"
            )
            if self.on_order_update:
                self.on_order_update(updated_order)

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order."""
        success = self.broker.cancel_order(order_id)
        if success and order_id in self._orders:
            self._orders[order_id].status = OrderStatus.CANCELLED
            if self.on_order_update:
                self.on_order_update(self._orders[order_id])
        return success

    def cancel_all_open_orders(self) -> int:
        """Cancel all currently open orders (used during EOD or square-off)."""
        cancelled_count = 0
        open_orders = self.get_open_orders()
        logger.info(f"Cancelling {len(open_orders)} open orders...")
        for order in open_orders:
            if self.cancel_order(order.order_id):
                cancelled_count += 1
        return cancelled_count

    def get_order(self, order_id: str) -> Optional[Order]:
        return self._orders.get(order_id)

    def get_open_orders(self) -> List[Order]:
        return [
            o for o in self._orders.values()
            if o.status in (OrderStatus.OPEN, OrderStatus.PENDING, OrderStatus.TRIGGER_PENDING)
        ]

    def get_all_orders(self) -> List[Order]:
        return list(self._orders.values())
