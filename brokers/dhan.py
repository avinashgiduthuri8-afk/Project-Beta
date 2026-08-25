"""
DhanHQ Broker Adapter.
Handles DhanHQ REST API authentication, margin fetching, positions, and order routing.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional
import requests
from config.config_loader import BotSettings
from core.enums import Exchange, OrderSide, OrderStatus, OrderType, ProductType
from core.interfaces import BaseBroker
from core.models import AccountBalance, Order, OrderRequest, Position

logger = logging.getLogger(__name__)


class DhanBroker(BaseBroker):
    """DhanHQ API Connector for Indian Equities and F&O Trading."""

    BASE_URL = "https://api.dhan.co/v2"

    def __init__(self, settings: BotSettings) -> None:
        self.settings = settings
        self.client_id = settings.env.dhan_client_id or ""
        self.access_token = settings.env.dhan_access_token or ""
        self._session = requests.Session()

    def authenticate(self) -> bool:
        if self.client_id and self.access_token:
            logger.info("DhanBroker: Initialized with client ID and permanent/daily access token.")
            return True
        logger.warning("DhanBroker: Missing Client ID or Access Token in configuration.")
        return False

    def is_authenticated(self) -> bool:
        return bool(self.client_id and self.access_token)

    def _get_headers(self) -> Dict[str, str]:
        return {
            "access-token": self.access_token,
            "client-id": self.client_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def get_profile(self) -> Dict[str, Any]:
        return {"client_id": self.client_id, "broker": "DhanHQ"}

    def get_funds(self) -> AccountBalance:
        try:
            resp = self._session.get(f"{self.BASE_URL}/fundlimit", headers=self._get_headers(), timeout=10)
            data = resp.json()
            avail = float(data.get("availabelBalance", 0.0))
            utilized = float(data.get("utilizedAmount", 0.0))
            return AccountBalance(
                available_cash=avail,
                available_margin=avail,
                utilized_margin=utilized,
                currency="INR",
            )
        except Exception as e:
            logger.error(f"Error fetching Dhan funds: {e}")
            return AccountBalance(available_cash=0.0, available_margin=0.0)

    def get_positions(self) -> List[Position]:
        try:
            resp = self._session.get(f"{self.BASE_URL}/positions", headers=self._get_headers(), timeout=10)
            data = resp.json() or []
            positions: List[Position] = []
            for p in data:
                positions.append(
                    Position(
                        symbol=p.get("tradingSymbol", ""),
                        exchange=Exchange(p.get("exchangeSegment", "NSE")),
                        product=ProductType(p.get("productType", "MIS")),
                        quantity=int(p.get("netQty", 0)),
                        buy_quantity=int(p.get("buyQty", 0)),
                        sell_quantity=int(p.get("sellQty", 0)),
                        buy_price=float(p.get("buyAvg", 0.0)),
                        sell_price=float(p.get("sellAvg", 0.0)),
                        last_price=float(p.get("costPrice", 0.0)),
                        realized_pnl=float(p.get("realizedProfit", 0.0)),
                        unrealized_pnl=float(p.get("unrealizedProfit", 0.0)),
                        pnl=float(p.get("realizedProfit", 0.0)) + float(p.get("unrealizedProfit", 0.0)),
                    )
                )
            return positions
        except Exception as e:
            logger.error(f"Error fetching Dhan positions: {e}")
            return []

    def get_order_book(self) -> List[Order]:
        try:
            resp = self._session.get(f"{self.BASE_URL}/orders", headers=self._get_headers(), timeout=10)
            data = resp.json() or []
            orders: List[Order] = []
            for o in data:
                orders.append(
                    Order(
                        order_id=str(o.get("orderId")),
                        symbol=o.get("tradingSymbol", ""),
                        exchange=Exchange(o.get("exchangeSegment", "NSE")),
                        side=OrderSide(o.get("transactionType", "BUY")),
                        order_type=OrderType(o.get("orderType", "LIMIT")),
                        product=ProductType(o.get("productType", "MIS")),
                        quantity=int(o.get("quantity", 0)),
                        filled_quantity=int(o.get("filledQty", 0)),
                        price=float(o.get("price", 0.0)) if o.get("price") else None,
                        status=OrderStatus.COMPLETE if o.get("orderStatus") == "TRADED" else OrderStatus.OPEN,
                    )
                )
            return orders
        except Exception as e:
            logger.error(f"Error fetching Dhan order book: {e}")
            return []

    def place_order(self, request: OrderRequest) -> Order:
        dhan_product_map = {
            ProductType.MIS: "INTRADAY",
            ProductType.CNC: "CNC",
            ProductType.NRML: "MARGIN",
        }
        dhan_ordertype_map = {
            OrderType.MARKET: "MARKET",
            OrderType.LIMIT: "LIMIT",
            OrderType.SL: "STOP_LOSS",
            OrderType.SL_M: "STOP_LOSS_MARKET",
        }
        payload = {
            "dhanClientId": self.client_id,
            "correlationId": f"PB_{int(time.time())}",
            "transactionType": request.side.value,
            "exchangeSegment": "NSE_EQ" if request.exchange == Exchange.NSE else "NSE_FNO",
            "productType": dhan_product_map.get(request.product, "INTRADAY"),
            "orderType": dhan_ordertype_map.get(request.order_type, "LIMIT"),
            "validity": "DAY",
            "tradingSymbol": request.symbol,
            "securityId": str(request.instrument_token or ""),
            "quantity": request.quantity,
            "price": request.price or 0.0,
            "triggerPrice": request.trigger_price or 0.0,
        }

        try:
            resp = self._session.post(f"{self.BASE_URL}/orders", json=payload, headers=self._get_headers(), timeout=10)
            res = resp.json()
            if res.get("orderStatus") in ("TRANSIT", "PENDING", "TRADED"):
                order_id = str(res.get("orderId"))
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
            return Order(
                order_id=f"REJ_{int(time.time())}",
                symbol=request.symbol,
                exchange=request.exchange,
                side=request.side,
                order_type=request.order_type,
                product=request.product,
                quantity=request.quantity,
                status=OrderStatus.REJECTED,
                status_message=res.get("remarks", "Rejected by Dhan"),
            )
        except Exception as e:
            logger.error(f"Dhan place order error: {e}")
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
        payload: Dict[str, Any] = {"dhanClientId": self.client_id, "orderId": order_id}
        if price:
            payload["price"] = price
        if trigger_price:
            payload["triggerPrice"] = trigger_price
        if quantity:
            payload["quantity"] = quantity

        resp = self._session.put(f"{self.BASE_URL}/orders/{order_id}", json=payload, headers=self._get_headers(), timeout=10)
        if resp.status_code == 200:
            return self.get_order_book()[0]
        raise RuntimeError(f"Dhan modify order failed: {resp.text}")

    def cancel_order(self, order_id: str) -> bool:
        try:
            resp = self._session.delete(f"{self.BASE_URL}/orders/{order_id}", headers=self._get_headers(), timeout=10)
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"Failed to cancel Dhan order {order_id}: {e}")
            return False
