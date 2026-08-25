"""Base Strategy class for Indian Market Trading Bots."""

from __future__ import annotations

import logging
import uuid
from typing import Optional, Dict, Any
from core.interfaces import BaseStrategy
from core.models import Tick, Candle, Order, OrderRequest
from core.enums import OrderSide, OrderType, ProductType, Exchange
from oms.execution_router import ExecutionRouter
from oms.order_manager import OrderManager

logger = logging.getLogger(__name__)


class Strategy(BaseStrategy):
    """Abstract Strategy with order submission helpers and lifecycle hooks."""

    def __init__(self, name: str, router: ExecutionRouter, order_manager: OrderManager):
        self.name = name
        self.router = router
        self.order_manager = order_manager

    def place_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        order_type: OrderType = OrderType.MARKET,
        product_type: ProductType = ProductType.MIS,
        exchange: Exchange = Exchange.NSE,
        price: Optional[float] = None,
        trigger_price: Optional[float] = None,
        lot_size: int = 1,
    ) -> Order:
        """Helper to create, route, and register a strategy order."""
        client_order_id = f"{self.name[:4].upper()}-{uuid.uuid4().hex[:6].upper()}"
        request = OrderRequest(
            client_order_id=client_order_id,
            symbol=symbol,
            exchange=exchange,
            side=side,
            order_type=order_type,
            product_type=product_type,
            quantity=quantity,
            price=price,
            trigger_price=trigger_price,
            tag=self.name,
        )

        order = self.router.route_order(request, lot_size=lot_size)
        self.order_manager.register_order(order)
        return order

    def on_tick(self, tick: Tick) -> None:
        pass

    def on_candle(self, candle: Candle) -> None:
        pass

    def on_order_update(self, order: Order) -> None:
        pass
