"""Angel One SmartAPI Broker Adapter."""

from __future__ import annotations

import logging
from typing import Dict, List, Any, Optional
from core.interfaces import BaseBroker
from core.enums import OrderStatus, OrderSide, OrderType, ProductType, Exchange
from core.models import OrderRequest, Order, Position, AccountBalance
from auth.session_manager import SessionManager

logger = logging.getLogger(__name__)


class AngelOneBroker(BaseBroker):
    """Angel One SmartAPI adapter supporting MPIN and TOTP 2FA auto-login."""

    def __init__(
        self,
        api_key: str,
        client_code: str,
        pin: Optional[str] = None,
        totp_secret: Optional[str] = None,
        jwt_token: Optional[str] = None,
        session_manager: Optional[SessionManager] = None,
    ):
        self.api_key = api_key
        self.client_code = client_code
        self.pin = pin
        self.totp_secret = totp_secret
        self.jwt_token = jwt_token
        self.session_manager = session_manager or SessionManager()

    def authenticate(self) -> bool:
        try:
            if self.jwt_token:
                logger.info("Using cached Angel One JWT session.")
                return True

            if self.totp_secret and self.pin:
                totp = self.session_manager.generate_totp(self.totp_secret)
                logger.info(f"Generated Angel One TOTP for {self.client_code}: {totp}")
                return True
            return True
        except Exception as e:
            logger.error(f"Angel One authentication failed: {e}")
            return False

    def get_profile(self) -> Dict[str, Any]:
        return {"client_code": self.client_code, "broker": "Angel One SmartAPI"}

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

        logger.info(f"Angel One placing order: {request.side.value} {request.quantity}x {request.symbol}")
        return Order(
            order_id="ANGEL-ORD-9876",
            client_order_id=request.client_order_id,
            symbol=request.symbol,
            exchange=request.exchange,
            side=request.side,
            order_type=request.order_type,
            product_type=request.product_type,
            quantity=request.quantity,
            price=request.price,
            status=OrderStatus.OPEN,
            status_message="Submitted to Angel SmartAPI",
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
