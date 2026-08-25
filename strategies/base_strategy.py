"""
Base Strategy Class for Project-Beta.
Provides order generation helpers, position context, and event handling hooks.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional
from core.enums import Exchange, OrderSide, OrderType, ProductType
from core.interfaces import BaseStrategy
from core.models import Candle, Order, OrderRequest, Tick

logger = logging.getLogger(__name__)


class Strategy(BaseStrategy):
    """
    Base strategy class to be inherited by all algorithmic trading strategies.
    """

    def __init__(
        self,
        name: str,
        submit_order_fn: Optional[Callable[[OrderRequest], Order]] = None,
    ) -> None:
        self.name = name
        self.submit_order_fn = submit_order_fn

    def on_tick(self, tick: Tick) -> None:
        """Invoked on each incoming tick."""
        pass

    def on_candle(self, candle: Candle) -> None:
        """Invoked on each completed or updated candle."""
        pass

    def on_order_update(self, order: Order) -> None:
        """Invoked when an order status changes."""
        logger.info(f"Strategy [{self.name}] received order update: {order.order_id} -> {order.status.value}")

    def buy(
        self,
        symbol: str,
        quantity: int,
        price: Optional[float] = None,
        product: ProductType = ProductType.MIS,
        exchange: Exchange = Exchange.NSE,
        order_type: OrderType = OrderType.LIMIT,
        tag: Optional[str] = None,
    ) -> Optional[Order]:
        """Convenience method to submit a BUY order."""
        if not self.submit_order_fn:
            logger.warning(f"Strategy [{self.name}] cannot place order: submit_order_fn is not set.")
            return None

        req = OrderRequest(
            symbol=symbol,
            exchange=exchange,
            side=OrderSide.BUY,
            order_type=order_type,
            product=product,
            quantity=quantity,
            price=price,
            tag=tag or self.name,
        )
        return self.submit_order_fn(req)

    def sell(
        self,
        symbol: str,
        quantity: int,
        price: Optional[float] = None,
        product: ProductType = ProductType.MIS,
        exchange: Exchange = Exchange.NSE,
        order_type: OrderType = OrderType.LIMIT,
        tag: Optional[str] = None,
    ) -> Optional[Order]:
        """Convenience method to submit a SELL order."""
        if not self.submit_order_fn:
            logger.warning(f"Strategy [{self.name}] cannot place order: submit_order_fn is not set.")
            return None

        req = OrderRequest(
            symbol=symbol,
            exchange=exchange,
            side=OrderSide.SELL,
            order_type=order_type,
            product=product,
            quantity=quantity,
            price=price,
            tag=tag or self.name,
        )
        return self.submit_order_fn(req)
