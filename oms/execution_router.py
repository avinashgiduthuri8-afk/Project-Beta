"""
Execution Router & Order Normalizer for Indian Equities and F&O.
Enforces tick size (₹0.05 step), lot size multiples, price normalization, and retry mechanics.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Optional
from config.config_loader import BotSettings
from core.enums import OrderType, ProductType
from core.interfaces import BaseBroker
from core.models import Order, OrderRequest

logger = logging.getLogger(__name__)


class ExecutionRouter:
    """
    Handles tick-size rounding, lot-size enforcement, retry handling, and routing to the active broker.
    """

    def __init__(self, broker: BaseBroker, settings: BotSettings) -> None:
        self.broker = broker
        self.settings = settings
        self.min_tick_size = settings.risk.min_tick_size or 0.05
        self.retry_attempts = settings.execution.order_retry_attempts
        self.retry_delay = settings.execution.order_retry_delay_sec

    @staticmethod
    def normalize_tick_price(price: Optional[float], tick_size: float = 0.05) -> Optional[float]:
        """
        Normalize price to the nearest tick step (e.g. ₹0.05 on NSE/BSE).
        Examples:
            100.03 -> 100.05
            100.02 -> 100.00
            100.07 -> 100.05
        """
        if price is None:
            return None
        rounded = round(round(price / tick_size) * tick_size, 2)
        return rounded

    @staticmethod
    def normalize_quantity(quantity: int, lot_size: int = 1) -> int:
        """
        Quantize order quantity to integer multiples of lot size (essential for NIFTY / BANKNIFTY F&O).
        """
        if lot_size <= 1:
            return max(1, quantity)
        # Round down or up to nearest lot size
        num_lots = max(1, math.floor(quantity / lot_size))
        return int(num_lots * lot_size)

    def route_order(self, request: OrderRequest, lot_size: int = 1, tick_size: float = 0.05) -> Order:
        """
        Normalize order request and route to broker with retry logic.
        """
        # Step 1: Normalize Price & Trigger Price
        if request.price is not None:
            request.price = self.normalize_tick_price(request.price, tick_size)

        if request.trigger_price is not None:
            request.trigger_price = self.normalize_tick_price(request.trigger_price, tick_size)

        # Step 2: Normalize Quantity
        request.quantity = self.normalize_quantity(request.quantity, lot_size)

        # Step 3: Route with retry loop
        last_order: Optional[Order] = None
        for attempt in range(1, self.retry_attempts + 1):
            try:
                logger.info(
                    f"Routing order (Attempt {attempt}/{self.retry_attempts}): "
                    f"{request.side.value} {request.quantity} {request.symbol} "
                    f"[{request.product.value}] @ {request.price or 'MKT'}"
                )
                order = self.broker.place_order(request)
                last_order = order

                # If successfully OPEN or COMPLETE, return immediately
                if order.status.value in ("OPEN", "COMPLETE", "TRIGGER_PENDING"):
                    return order

                logger.warning(
                    f"Order placement returned non-active status: {order.status.value} - {order.status_message}"
                )

            except Exception as e:
                logger.error(f"Routing attempt {attempt} failed with exception: {e}")

            if attempt < self.retry_attempts:
                time.sleep(self.retry_delay * attempt)

        if last_order:
            return last_order

        raise RuntimeError(f"Failed to place order for {request.symbol} after {self.retry_attempts} attempts.")
