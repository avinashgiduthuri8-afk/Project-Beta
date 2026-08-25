"""
Angel One SmartAPI Broker Adapter.
Handles SmartAPI TOTP login, historical data, and order execution.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional
import requests
from auth.session_manager import SessionManager
from config.config_loader import BotSettings
from core.enums import Exchange, OrderSide, OrderStatus, OrderType, ProductType
from core.interfaces import BaseBroker
from core.models import AccountBalance, Order, OrderRequest, Position

logger = logging.getLogger(__name__)


class AngelOneBroker(BaseBroker):
    """Angel One SmartAPI Adapter with automated TOTP session management."""

    BASE_URL = "https://apiconnect.angelbroking.com"

    def __init__(self, settings: BotSettings, session_manager: Optional[SessionManager] = None) -> None:
        self.settings = settings
        self.api_key = settings.env.angel_api_key or ""
        self.client_code = settings.env.angel_client_code or ""
        self.password = settings.env.angel_password or ""
        self.totp_secret = settings.env.angel_totp_secret or ""

        self.session_mgr = session_manager or SessionManager(settings.storage.session_cache_path)
        self.jwt_token: Optional[str] = None
        self.feed_token: Optional[str] = None
        self._session = requests.Session()

    def authenticate(self) -> bool:
        cached_token = self.session_mgr.get_valid_session("ANGEL_ONE")
        if cached_token:
            self.jwt_token = cached_token
            logger.info("AngelOneBroker: Reusing valid cached session token.")
            return True

        if not (self.api_key and self.client_code and self.password and self.totp_secret):
            logger.warning("Angel One credentials incomplete. Unable to authenticate.")
            return False

        try:
            totp = SessionManager.generate_totp(self.totp_secret)
            payload = {
                "clientcode": self.client_code,
                "password": self.password,
                "totp": totp,
            }
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-UserType": "USER",
                "X-SourceID": "WEB",
                "X-ClientLocalIP": "127.0.0.1",
                "X-ClientPublicIP": "106.193.147.98",
                "X-MACAddress": "fe80::216e:6507:4b90:3719",
                "X-PrivateKey": self.api_key,
            }

            resp = self._session.post(
                f"{self.BASE_URL}/rest/auth/partner/v1/loginByPassword",
                json=payload,
                headers=headers,
                timeout=10,
            )
            data = resp.json()
            if data.get("status") is True:
                self.jwt_token = data["data"]["jwtToken"]
                self.feed_token = data["data"]["feedToken"]
                self.session_mgr.cache_session("ANGEL_ONE", self.jwt_token)
                logger.info("Angel One SmartAPI authentication successful.")
                return True
            else:
                logger.error(f"Angel One auth failed: {data.get('message')}")
                return False
        except Exception as e:
            logger.error(f"Angel One auth exception: {e}")
            return False

    def is_authenticated(self) -> bool:
        return self.jwt_token is not None

    def _get_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.jwt_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-UserType": "USER",
            "X-SourceID": "WEB",
            "X-PrivateKey": self.api_key,
        }

    def get_profile(self) -> Dict[str, Any]:
        try:
            resp = self._session.get(f"{self.BASE_URL}/rest/secure/angelbroking/user/v1/getProfile", headers=self._get_headers(), timeout=10)
            return resp.json().get("data", {})
        except Exception as e:
            logger.error(f"Error fetching Angel One profile: {e}")
            return {}

    def get_funds(self) -> AccountBalance:
        try:
            resp = self._session.get(f"{self.BASE_URL}/rest/secure/angelbroking/user/v1/getRMS", headers=self._get_headers(), timeout=10)
            data = resp.json().get("data", {})
            net_avail = float(data.get("net", 0.0))
            utilized = float(data.get("utilizedAmount", 0.0))
            return AccountBalance(
                available_cash=net_avail,
                available_margin=net_avail,
                utilized_margin=utilized,
                currency="INR",
            )
        except Exception as e:
            logger.error(f"Error fetching Angel One RMS funds: {e}")
            return AccountBalance(available_cash=0.0, available_margin=0.0)

    def get_positions(self) -> List[Position]:
        try:
            resp = self._session.get(f"{self.BASE_URL}/rest/secure/angelbroking/order/v1/getPosition", headers=self._get_headers(), timeout=10)
            data = resp.json().get("data", []) or []
            positions: List[Position] = []
            for p in data:
                positions.append(
                    Position(
                        symbol=p.get("tradingsymbol", ""),
                        exchange=Exchange(p.get("exchange", "NSE")),
                        product=ProductType(p.get("producttype", "MIS")),
                        quantity=int(p.get("netqty", 0)),
                        buy_quantity=int(p.get("buyqty", 0)),
                        sell_quantity=int(p.get("sellqty", 0)),
                        buy_price=float(p.get("buyavgprice", 0.0)),
                        sell_price=float(p.get("sellavgprice", 0.0)),
                        last_price=float(p.get("ltp", 0.0)),
                        pnl=float(p.get("pnl", 0.0)),
                        realized_pnl=float(p.get("realised", 0.0)),
                        unrealized_pnl=float(p.get("unrealised", 0.0)),
                    )
                )
            return positions
        except Exception as e:
            logger.error(f"Error fetching Angel One positions: {e}")
            return []

    def get_order_book(self) -> List[Order]:
        try:
            resp = self._session.get(f"{self.BASE_URL}/rest/secure/angelbroking/order/v1/getOrderBook", headers=self._get_headers(), timeout=10)
            data = resp.json().get("data", []) or []
            orders: List[Order] = []
            for o in data:
                orders.append(
                    Order(
                        order_id=str(o.get("orderid")),
                        symbol=o.get("tradingsymbol", ""),
                        exchange=Exchange(o.get("exchange", "NSE")),
                        side=OrderSide(o.get("transactiontype", "BUY")),
                        order_type=OrderType(o.get("ordertype", "LIMIT")),
                        product=ProductType(o.get("producttype", "MIS")),
                        quantity=int(o.get("quantity", 0)),
                        filled_quantity=int(o.get("filledshares", 0)),
                        pending_quantity=int(o.get("unfilledshares", 0)),
                        price=float(o.get("price", 0.0)) if o.get("price") else None,
                        status=OrderStatus.COMPLETE if o.get("orderstatus") == "complete" else OrderStatus.OPEN,
                    )
                )
            return orders
        except Exception as e:
            logger.error(f"Error fetching Angel One order book: {e}")
            return []

    def place_order(self, request: OrderRequest) -> Order:
        payload = {
            "variety": "NORMAL",
            "tradingsymbol": request.symbol,
            "symboltoken": str(request.instrument_token or ""),
            "transactiontype": request.side.value,
            "exchange": request.exchange.value,
            "ordertype": request.order_type.value,
            "producttype": request.product.value,
            "duration": "DAY",
            "price": str(request.price or "0"),
            "squareoff": "0",
            "stoploss": "0",
            "quantity": str(request.quantity),
        }
        if request.trigger_price:
            payload["triggerprice"] = str(request.trigger_price)

        try:
            resp = self._session.post(
                f"{self.BASE_URL}/rest/secure/angelbroking/order/v1/placeOrder",
                json=payload,
                headers=self._get_headers(),
                timeout=10,
            )
            res = resp.json()
            if res.get("status") is True:
                order_id = str(res["data"]["orderid"])
                return Order(
                    order_id=order_id,
                    symbol=request.symbol,
                    exchange=request.exchange,
                    side=request.side,
                    order_type=request.order_type,
                    product=request.product,
                    quantity=request.quantity,
                    price=request.price,
                    status=OrderStatus.OPEN,
                )
            else:
                return Order(
                    order_id=f"REJ_{int(time.time())}",
                    symbol=request.symbol,
                    exchange=request.exchange,
                    side=request.side,
                    order_type=request.order_type,
                    product=request.product,
                    quantity=request.quantity,
                    status=OrderStatus.REJECTED,
                    status_message=res.get("message"),
                )
        except Exception as e:
            logger.error(f"Angel One order error: {e}")
            return Order(
                order_id=f"ERR_{int(time.time())}",
                symbol=request.symbol,
                exchange=request.exchange,
                side=request.side,
                order_type=request.order_type,
                product=request.product,
                quantity=request.quantity,
                status=OrderStatus.REJECTED,
                status_message=str(e),
            )

    def modify_order(
        self,
        order_id: str,
        price: Optional[float] = None,
        trigger_price: Optional[float] = None,
        quantity: Optional[int] = None,
    ) -> Order:
        payload: Dict[str, Any] = {"orderid": order_id, "variety": "NORMAL"}
        if price:
            payload["price"] = str(price)
        if trigger_price:
            payload["triggerprice"] = str(trigger_price)
        if quantity:
            payload["quantity"] = str(quantity)

        resp = self._session.post(
            f"{self.BASE_URL}/rest/secure/angelbroking/order/v1/modifyOrder",
            json=payload,
            headers=self._get_headers(),
            timeout=10,
        )
        if resp.json().get("status") is True:
            return self.get_order_book()[0]
        raise RuntimeError(f"Angel One modify failed: {resp.json().get('message')}")

    def cancel_order(self, order_id: str) -> bool:
        try:
            resp = self._session.post(
                f"{self.BASE_URL}/rest/secure/angelbroking/order/v1/cancelOrder",
                json={"variety": "NORMAL", "orderid": order_id},
                headers=self._get_headers(),
                timeout=10,
            )
            return resp.json().get("status") is True
        except Exception as e:
            logger.error(f"Failed to cancel Angel One order: {e}")
            return False
