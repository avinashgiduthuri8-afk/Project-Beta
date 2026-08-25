"""Execution Router with Indian Market Tick and Lot Size Normalization."""

from __future__ import annotations

import logging
import math
from typing import Optional
from core.models import OrderRequest, Order
from core.enums import OrderType, Exchange
from core.interfaces import BaseBroker

logger = logging.getLogger(__name__)


class ExecutionRouter:
    """Smart Order Router with NSE/BSE tick-size (₹0.05) and lot-size normalization."""

    def __init__(self, broker: BaseBroker, default_tick_size: float = 0.05, max_retries: int = 3):
        self.broker = broker
        self.default_tick_size = default_tick_size
        self.max_retries = max_retries

    @staticmethod
    def normalize_tick_size(price: float, tick_size: float = 0.05) -> float:
        """Round price to the nearest tick size multiple (e.g. ₹0.05 for NSE)."""
        if price <= 0:
            return price
        # Round to nearest multiple of tick_size
        ticks = round(price / tick_size)
        return round(ticks * tick_size, 2)

    @staticmethod
    def normalize_lot_size(quantity: int, lot_size: int = 1) -> int:
        """Ensure order quantity is a valid integer multiple of the lot size."""
        if lot_size <= 1:
            return max(1, quantity)
        multiplier = max(1, round(quantity / lot_size))
        return multiplier * lot_size

    def route_order(self, request: OrderRequest, lot_size: int = 1, tick_size: Optional[float] = None) -> Order:
        """Sanitize order request and route to broker with retry mechanism."""
        used_tick_size = tick_size or self.default_tick_size

        # 1. Normalize quantity
        normalized_qty = self.normalize_lot_size(request.quantity, lot_size)
        request.quantity = normalized_qty

        # 2. Normalize price
        if request.price is not None and request.order_type in (OrderType.LIMIT, OrderType.SL):
            request.price = self.normalize_tick_size(request.price, used_tick_size)

        # 3. Normalize trigger price
        if request.trigger_price is not None and request.order_type in (OrderType.SL, OrderType.SL_M):
            request.trigger_price = self.normalize_tick_size(request.trigger_price, used_tick_size)

        # 4. Route to broker with retries
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                order = self.broker.place_order(request)
                logger.info(f"Order {order.order_id} routed successfully on attempt {attempt}")
                return order
            except Exception as e:
                last_error = e
                logger.warning(f"Order placement attempt {attempt}/{self.max_retries} failed: {e}")

        raise RuntimeError(f"Failed to place order after {self.max_retries} attempts: {last_error}")
