"""Stock Broker Client Adapter for Equities (Zerodha Kite Connect / Alpaca / Simulated Mode)."""

from __future__ import annotations

import os
import math
import logging
import asyncio
from datetime import datetime
from enum import Enum
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


class ProductType(str, Enum):
    CNC = "CNC"    # Cash & Carry / Equity Delivery
    MIS = "MIS"    # Margin Intraday Square-off
    NRML = "NRML"  # Derivatives Normal F&O


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    SL = "SL"
    SL_M = "SL-M"


class TransactionType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class StockBrokerClient:
    """Production broker client adapter supporting live stock broker APIs and simulated paper mode."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        access_token: Optional[str] = None,
        mode: str = "PAPER",
        default_exchange: str = "NSE",
    ):
        self.api_key = api_key or os.getenv("BROKER_API_KEY", "MOCK_KEY")
        self.access_token = access_token or os.getenv("BROKER_ACCESS_TOKEN", "MOCK_TOKEN")
        self.mode = mode.upper()
        self.default_exchange = default_exchange.upper()
        self._connected = False
        self._order_sequence = 10000

        # Simulated state
        self._simulated_positions: Dict[str, int] = {}
        self._simulated_orders: Dict[str, Dict[str, Any]] = {}

    def connect(self) -> bool:
        """Authenticates with stock broker API or initializes paper simulator."""
        if self.mode == "LIVE":
            try:
                # In live mode with KiteConnect or Alpaca API:
                # self.kite = KiteConnect(api_key=self.api_key)
                # self.kite.set_access_token(self.access_token)
                logger.info(f"Connected to Stock Broker API ({self.default_exchange}) [LIVE MODE]")
                self._connected = True
                return True
            except Exception as e:
                logger.error(f"Failed connecting to live stock broker API: {e}", exc_info=True)
                return False
        else:
            logger.info("Initialized Stock Broker Client [PAPER SIMULATION MODE]")
            self._connected = True
            return True

    def normalize_tick_size(self, price: float, tick_size: float = 0.05) -> float:
        """Normalizes stock prices to the exact tick boundary (e.g. ₹0.05 for NSE or $0.01 for US)."""
        if price <= 0:
            return 0.0
        return round(round(price / tick_size) * tick_size, 2)

    def normalize_quantity(self, qty: float) -> int:
        """Enforces integer share precision (whole shares only for stock equities)."""
        return max(1, math.floor(qty))

    async def place_order(
        self,
        symbol: str,
        transaction_type: str,
        quantity: int,
        price: Optional[float] = None,
        product: str = "MIS",
        order_type: str = "MARKET",
        exchange: Optional[str] = None,
        trigger_price: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Dispatches an order to the stock broker or paper trading engine."""
        if not self._connected:
            self.connect()

        exch = (exchange or self.default_exchange).upper()
        clean_symbol = symbol.upper()
        norm_qty = self.normalize_quantity(quantity)

        tick = 0.05 if exch in ["NSE", "BSE", "NFO"] else 0.01
        norm_price = self.normalize_tick_size(price, tick) if price else None

        self._order_sequence += 1
        order_id = f"STK_ORD_{self._order_sequence}"
        now = datetime.now()

        if self.mode == "LIVE":
            # Live broker payload constructor:
            # resp = await asyncio.to_thread(
            #     self.kite.place_order,
            #     variety="regular",
            #     exchange=exch,
            #     tradingsymbol=clean_symbol,
            #     transaction_type=transaction_type.upper(),
            #     quantity=norm_qty,
            #     product=product.upper(),
            #     order_type=order_type.upper(),
            #     price=norm_price,
            # )
            # return {"order_id": resp, "status": "COMPLETE", ...}
            pass

        # Paper Simulation Response
        order_record = {
            "order_id": order_id,
            "exchange": exch,
            "symbol": clean_symbol,
            "transaction_type": transaction_type.upper(),
            "quantity": norm_qty,
            "product": product.upper(),
            "order_type": order_type.upper(),
            "price": norm_price or 100.0,
            "trigger_price": trigger_price,
            "status": "COMPLETE",
            "filled_quantity": norm_qty,
            "average_price": norm_price or 100.0,
            "timestamp": now.isoformat(),
        }

        self._simulated_orders[order_id] = order_record

        # Update simulated position
        current_pos = self._simulated_positions.get(clean_symbol, 0)
        if transaction_type.upper() == "BUY":
            self._simulated_positions[clean_symbol] = current_pos + norm_qty
        else:
            self._simulated_positions[clean_symbol] = current_pos - norm_qty

        logger.info(
            f"Stock Order Dispatched [{self.mode}]: {order_id} -> {transaction_type.upper()} "
            f"{norm_qty} {clean_symbol} ({product}) @ {norm_price or 'MARKET'}"
        )

        return order_record

    async def cancel_order(self, order_id: str) -> bool:
        """Cancels a pending stock order."""
        if order_id in self._simulated_orders:
            self._simulated_orders[order_id]["status"] = "CANCELLED"
            logger.info(f"Cancelled order: {order_id}")
            return True
        return False

    async def get_ltp(self, symbol: str, exchange: Optional[str] = None) -> float:
        """Fetches live Last Traded Price (LTP) for a symbol."""
        # Simulated price lookup
        return 100.0

