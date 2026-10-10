"""Safe-by-default Zerodha, Dhan, and paper broker adapters.

No credential is bundled with Project Beta. Public data is handled separately;
these adapters only contact a broker when explicitly configured for live trading.
"""

from __future__ import annotations

import csv
import io
import time
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

import httpx


class BrokerConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class BrokerOrder:
    symbol: str
    side: str
    quantity: int
    exchange: str = "NSE"
    product: str = "CNC"
    order_type: str = "MARKET"
    price: float | None = None
    trigger_price: float | None = None
    client_order_id: str | None = None
    security_id: str | None = None

    def __post_init__(self) -> None:
        if self.side.upper() not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        if self.quantity < 1 or int(self.quantity) != self.quantity:
            raise ValueError("Indian cash-equity quantity must be a positive whole number")
        if self.exchange.upper() not in {"NSE", "BSE"}:
            raise ValueError("exchange must be NSE or BSE")


class Broker(Protocol):
    async def place_order(self, order: BrokerOrder) -> dict[str, Any]: ...
    async def cancel_order(self, order_id: str) -> dict[str, Any]: ...
    async def close(self) -> None: ...


class _HttpBroker:
    def __init__(self, timeout: float = 15.0, client: httpx.AsyncClient | None = None) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        if self._owns_client and not self._client.is_closed:
            await self._client.aclose()

    @staticmethod
    def _error(response: httpx.Response) -> dict[str, Any]:
        try:
            body: Any = response.json()
        except ValueError:
            body = response.text[:500]
        return {
            "success": False,
            "error": f"HTTP_{response.status_code}",
            "status_code": response.status_code,
            "message": body,
        }


class PaperBroker:
    """In-memory broker used when no live credentials are configured."""

    def __init__(self) -> None:
        self.orders: dict[str, dict[str, Any]] = {}

    async def place_order(self, order: BrokerOrder) -> dict[str, Any]:
        order_id = f"PAPER-{uuid.uuid4().hex[:16].upper()}"
        record = {
            "order_id": order_id,
            "status": "FILLED",
            "symbol": order.symbol,
            "exchange": order.exchange,
            "side": order.side.upper(),
            "quantity": order.quantity,
            "filled_quantity": order.quantity,
            "price": float(order.price or 0.0),
            "product": order.product,
            "created_at": int(time.time()),
        }
        self.orders[order_id] = record
        return {"success": True, "paper": True, "order_id": order_id, "order": record}

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        record = self.orders.get(order_id)
        if not record:
            return {"success": False, "error": "ORDER_NOT_FOUND"}
        record["status"] = "CANCELLED"
        return {"success": True, "paper": True, "order_id": order_id}

    async def close(self) -> None:
        return None


class ZerodhaBroker(_HttpBroker):
    BASE_URL = "https://api.kite.trade"

    def __init__(
        self,
        api_key: str,
        access_token: str,
        *,
        timeout: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key or not access_token:
            raise BrokerConfigurationError("Zerodha requires api_key and access_token")
        super().__init__(timeout, client)
        self.headers = {
            "X-Kite-Version": "3",
            "Authorization": f"token {api_key}:{access_token}",
        }

    async def place_order(self, order: BrokerOrder) -> dict[str, Any]:
        order_type = order.order_type.upper().replace("STOP_LIMIT", "SL")
        payload: dict[str, Any] = {
            "tradingsymbol": order.symbol.upper().removeprefix("NSE:").removeprefix("BSE:"),
            "exchange": order.exchange.upper(),
            "transaction_type": order.side.upper(),
            "order_type": order_type,
            "quantity": order.quantity,
            "product": order.product.upper(),
            "validity": "DAY",
        }
        if order.client_order_id:
            payload["tag"] = order.client_order_id[:20]
        if order_type != "MARKET" and order.price is not None:
            payload["price"] = order.price
        if order.trigger_price is not None:
            payload["trigger_price"] = order.trigger_price
        response = await self._client.post(
            f"{self.BASE_URL}/orders/regular", headers=self.headers, data=payload
        )
        if response.is_error:
            return self._error(response)
        data = response.json().get("data") or {}
        order_id = str(data.get("order_id") or "")
        return {
            "success": bool(order_id),
            "broker": "ZERODHA",
            "order_id": order_id,
            "exchange_order_id": order_id,
            "order": {**payload, **data},
        }

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        response = await self._client.delete(
            f"{self.BASE_URL}/orders/regular/{order_id}", headers=self.headers
        )
        if response.is_error:
            return self._error(response)
        return {"success": True, "broker": "ZERODHA", "order_id": order_id}


class DhanBroker(_HttpBroker):
    BASE_URL = "https://api.dhan.co/v2"
    INSTRUMENT_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"

    def __init__(
        self,
        client_id: str,
        access_token: str,
        *,
        timeout: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not client_id or not access_token:
            raise BrokerConfigurationError("Dhan requires client_id and access_token")
        super().__init__(timeout, client)
        self.client_id = client_id
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "access-token": access_token,
            "dhanClientId": client_id,
        }
        self._security_ids: dict[tuple[str, str], str] = {}

    async def _load_instruments(self) -> None:
        if self._security_ids:
            return
        response = await self._client.get(self.INSTRUMENT_MASTER_URL)
        response.raise_for_status()
        reader = csv.DictReader(io.StringIO(response.text))
        for row in reader:
            exchange = str(row.get("SEM_EXM_EXCH_ID") or row.get("EXCH_ID") or "").upper()
            segment = str(row.get("SEM_SEGMENT") or row.get("SEGMENT") or "").upper()
            series = str(row.get("SEM_SERIES") or row.get("SERIES") or "").upper()
            symbol = str(
                row.get("SEM_TRADING_SYMBOL")
                or row.get("SYMBOL_NAME")
                or row.get("SM_SYMBOL_NAME")
                or ""
            ).upper()
            security_id = str(
                row.get("SEM_SMST_SECURITY_ID")
                or row.get("SECURITY_ID")
                or row.get("SEM_SECURITY_ID")
                or ""
            )
            if exchange in {"NSE", "BSE"} and segment == "E" and series in {"", "EQ", "A"}:
                if symbol and security_id:
                    self._security_ids[(exchange, symbol)] = security_id

    async def resolve_security_id(self, exchange: str, symbol: str) -> str:
        await self._load_instruments()
        security_id = self._security_ids.get((exchange.upper(), symbol.upper()))
        if not security_id:
            raise BrokerConfigurationError(
                f"Dhan security ID not found for {exchange}:{symbol}; pass security_id explicitly"
            )
        return security_id

    async def place_order(self, order: BrokerOrder) -> dict[str, Any]:
        security_id = order.security_id or await self.resolve_security_id(
            order.exchange, order.symbol
        )
        order_type_map = {
            "MARKET": "MARKET",
            "LIMIT": "LIMIT",
            "SL": "STOP_LOSS",
            "STOP_LIMIT": "STOP_LOSS",
            "SL-M": "STOP_LOSS_MARKET",
        }
        payload: dict[str, Any] = {
            "dhanClientId": self.client_id,
            "correlationId": (order.client_order_id or uuid.uuid4().hex)[:25],
            "transactionType": order.side.upper(),
            "exchangeSegment": f"{order.exchange.upper()}_EQ",
            "productType": "INTRADAY" if order.product.upper() == "MIS" else "CNC",
            "orderType": order_type_map.get(order.order_type.upper(), order.order_type.upper()),
            "validity": "DAY",
            "securityId": str(security_id),
            "quantity": order.quantity,
            "disclosedQuantity": 0,
            "price": float(order.price or 0.0),
            "triggerPrice": float(order.trigger_price or 0.0),
            "afterMarketOrder": False,
        }
        response = await self._client.post(
            f"{self.BASE_URL}/orders", headers=self.headers, json=payload
        )
        if response.is_error:
            return self._error(response)
        data = response.json()
        order_id = str(data.get("orderId") or data.get("order_id") or "")
        return {
            "success": bool(order_id),
            "broker": "DHAN",
            "order_id": order_id,
            "exchange_order_id": order_id,
            "order": {**payload, **data},
        }

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        response = await self._client.delete(
            f"{self.BASE_URL}/orders/{order_id}", headers=self.headers
        )
        if response.is_error:
            return self._error(response)
        return {"success": True, "broker": "DHAN", "order_id": order_id}


def build_broker(config: Any) -> Broker:
    """Create the configured broker, defaulting to paper instead of guessing keys."""
    name = str(getattr(config, "broker", "PAPER") or "PAPER").upper()
    if name == "PAPER":
        return PaperBroker()
    if name == "ZERODHA":
        return ZerodhaBroker(
            api_key=str(getattr(config, "zerodha_api_key", "") or ""),
            access_token=str(getattr(config, "zerodha_access_token", "") or ""),
        )
    if name == "DHAN":
        return DhanBroker(
            client_id=str(getattr(config, "dhan_client_id", "") or ""),
            access_token=str(getattr(config, "dhan_access_token", "") or ""),
        )
    raise BrokerConfigurationError(f"Unsupported BROKER={name}; use PAPER, ZERODHA, or DHAN")
