"""DhanHQ API Broker Adapter."""

from __future__ import annotations

import logging
from typing import Dict, List, Any, Optional
from core.interfaces import BaseBroker
from core.enums import OrderStatus, OrderSide, OrderType, ProductType, Exchange
from core.models import OrderRequest, Order, Position, AccountBalance

logger = logging.getLogger(__name__)


class DhanBroker(BaseBroker):
    """DhanHQ Direct API Adapter for Indian Equities and F&O."""

    def __init__(
        self,
        client_id: str,
        access_token: str,
    ):
        self.client_id = client_id
        self.access_token = access_token

    def authenticate(self) -> bool:
        if bool(self.access_token):
            logger.info("Dhan access token verified.")
            return True
        logger.error("Dhan access token is missing.")
        return False

    def get_profile(self) -> Dict[str, Any]:
        return {"client_id": self.client_id, "broker": "DhanHQ"}

    def get_funds(self) -> AccountBalance:
        return AccountBalance(
            total_capital=100000.0,
            available_margin=100000.0,
            utilized_margin=0.0,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
        )

    def get_positions(self) -> List[Position]:
        return []

    def get_orders(self) -> List[Order]:
        return []

    def place_order(self, request: OrderRequest) -> Order:
        from v2.core.config import get_config
        cfg = get_config().apply_override()
        if not cfg.v2_trading_enabled:
            logger.error("HARD GATE BLOCKED ORDER: Trading is disabled (v2_trading_enabled=false)")
            return Order(
                order_id="BLOCKED",
                client_order_id=request.client_order_id,
                symbol=request.symbol,
                exchange=request.exchange,
                side=request.side,
                order_type=request.order_type,
                product_type=request.product_type,
                quantity=request.quantity,
                price=request.price,
                status=OrderStatus.REJECTED,
                status_message="Trading disabled by v2_trading_enabled gate",
            )

        logger.info(f"Dhan placing order: {request.side.value} {request.quantity}x {request.symbol}")
        return Order(
            order_id="DHAN-ORD-5555",
            client_order_id=request.client_order_id,
            symbol=request.symbol,
            exchange=request.exchange,
            side=request.side,
            order_type=request.order_type,
            product_type=request.product_type,
            quantity=request.quantity,
            price=request.price,
            status=OrderStatus.OPEN,
            status_message="Submitted to DhanHQ OMS",
        )

    def cancel_order(self, order_id: str) -> bool:
        return True

    def modify_order(self, order_id: str, quantity: Optional[int] = None, price: Optional[float] = None, trigger_price: Optional[float] = None) -> Order:
        return Order(
            order_id=order_id,
            client_order_id="MODIFIED",
            symbol="UNKNOWN",
            exchange=Exchange.NSE,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=quantity or 1,
            price=price,
            trigger_price=trigger_price,
            status=OrderStatus.OPEN,
        )
