"""In-memory Order Manager and Order Lifecycle State Machine."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List, Optional, Callable
from core.models import Order, OrderRequest
from core.enums import OrderStatus

logger = logging.getLogger(__name__)


class OrderManager:
    """Manages active/historical orders and validates state machine transitions."""

    # Valid state transitions
    VALID_TRANSITIONS = {
        OrderStatus.PENDING: {OrderStatus.OPEN, OrderStatus.COMPLETE, OrderStatus.REJECTED, OrderStatus.CANCELLED},
        OrderStatus.OPEN: {OrderStatus.TRIGGER_PENDING, OrderStatus.COMPLETE, OrderStatus.CANCELLED, OrderStatus.REJECTED},
        OrderStatus.TRIGGER_PENDING: {OrderStatus.OPEN, OrderStatus.COMPLETE, OrderStatus.CANCELLED},
        OrderStatus.COMPLETE: set(),  # Terminal state
        OrderStatus.CANCELLED: set(), # Terminal state
        OrderStatus.REJECTED: set(),  # Terminal state
    }

    def __init__(self):
        self.orders: Dict[str, Order] = {}
        self.client_order_map: Dict[str, str] = {}
        self.listeners: List[Callable[[Order], None]] = []

    def add_listener(self, listener: Callable[[Order], None]) -> None:
        """Register a callback for order status updates."""
        self.listeners.append(listener)

    def register_order(self, order: Order) -> None:
        """Register a newly placed order."""
        self.orders[order.order_id] = order
        self.client_order_map[order.client_order_id] = order.order_id
        self._notify_listeners(order)

    def update_order_status(
        self,
        order_id: str,
        new_status: OrderStatus,
        filled_qty: Optional[int] = None,
        avg_price: Optional[float] = None,
        message: Optional[str] = None,
    ) -> Order:
        """Apply state transition with safety validation."""
        if order_id not in self.orders:
            raise KeyError(f"Order ID {order_id} not registered.")

        order = self.orders[order_id]
        current_status = order.status

        if current_status == new_status:
            return order

        allowed = self.VALID_TRANSITIONS.get(current_status, set())
        if new_status not in allowed:
            logger.warning(
                f"Invalid order status transition for {order_id}: {current_status} -> {new_status}"
            )

        order.status = new_status
        order.updated_at = datetime.now()
        if filled_qty is not None:
            order.filled_quantity = filled_qty
            order.pending_quantity = max(0, order.quantity - filled_qty)
        if avg_price is not None:
            order.average_price = avg_price
        if message:
            order.status_message = message

        self._notify_listeners(order)
        return order

    def get_order(self, order_id: str) -> Optional[Order]:
        return self.orders.get(order_id)

    def get_open_orders(self) -> List[Order]:
        return [
            o for o in self.orders.values()
            if o.status in (OrderStatus.PENDING, OrderStatus.OPEN, OrderStatus.TRIGGER_PENDING)
        ]

    def _notify_listeners(self, order: Order) -> None:
        for listener in self.listeners:
            try:
                listener(order)
            except Exception as e:
                logger.error(f"Error in order listener: {e}")
