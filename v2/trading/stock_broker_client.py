"""Stock Broker Client Adapter for Equities (Zerodha Kite Connect / Alpaca / Simulated Mode)."""

from __future__ import annotations

import os
import math
import logging
import asyncio
from datetime import datetime
from enum import Enum
from typing import Dict, Any, Optional, List
from typing import Dict, Any, Optional, List, Tuple

from v2.core.config import get_config
from v2.trading.order_lifecycle import OrderLifecycleState, OrderLifecycleTracker

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
        live_adapter: Optional[Any] = None,
    ):
        self.api_key = api_key or os.getenv("BROKER_API_KEY", "MOCK_KEY")
        self.access_token = access_token or os.getenv("BROKER_ACCESS_TOKEN", "MOCK_TOKEN")
        self.mode = mode.upper()
        self.default_exchange = default_exchange.upper()
        self.live_adapter = live_adapter
        self._connected = False
        self._order_sequence = 10000

        # Simulated state
        self._simulated_positions: Dict[str, int] = {}
        self._simulated_orders: Dict[str, Dict[str, Any]] = {}

    def connect(self) -> bool:
        """Authenticates with stock broker API or initializes paper simulator."""
        if self.mode == "LIVE":
            if self.live_adapter is not None:
                # Check if already connected
                try:
                    if getattr(self.live_adapter, "is_connected", lambda: False)():
                        self._connected = True
                        logger.info(f"Connected to Stock Broker API ({self.default_exchange}) [LIVE MODE]")
                        return True
                except Exception as e:
                    logger.error(f"Error checking live adapter connection state: {e}", exc_info=True)

                # Attempt to connect
                if hasattr(self.live_adapter, "connect"):
                    try:
                        ok = self.live_adapter.connect()
                        if ok:
                            self._connected = True
                            logger.info(f"Connected to Stock Broker API via adapter [LIVE MODE]")
                            return True
                    except Exception as e:
                        logger.error(f"Failed connecting to live stock broker API: {e}", exc_info=True)
                        self._connected = False
                        return False

            # No valid configured live adapter available in LIVE mode
            logger.error(f"LIVE mode connection failed: No valid live broker adapter configured for {self.default_exchange}. Failing closed.")
            self._connected = False
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

    def validate_execution_response(self, response: Any) -> Tuple[bool, str, Dict[str, Any]]:
        """Authoritative validation of broker order responses (BETA-CODE-03).

        Normalizes raw broker payloads (or internal Order objects) into a standard dictionary contract.
        Validates the state and determines if a position can be safely opened.
        """
        # 1. Normalize payload into a dictionary contract
        normalized = {}
        if isinstance(response, dict):
            normalized = response.copy()
        elif hasattr(response, "__dict__"):
            # e.g., v1 Order model object
            normalized = {
                "order_id": getattr(response, "order_id", None),
                "status": getattr(response, "status", None),
                "filled_quantity": getattr(response, "filled_quantity", 0),
                "average_price": getattr(response, "average_price", 0.0),
                "reason": getattr(response, "status_message", ""),
            }
            if hasattr(response, "status") and hasattr(response.status, "value"):
                normalized["status"] = response.status.value
        else:
            return False, f"Invalid execution response type: {type(response)}", {}

        # 2. Extract and validate Order ID
        order_id = normalized.get("order_id")
        if not order_id:
            reason = normalized.get("reason") or normalized.get("error_details") or "Missing order_id in broker response"
            logger.error(f"Execution Response Validation Failed: {reason}")
            normalized["status"] = "FAILED"
            normalized["reason"] = reason
            return False, str(reason), normalized

        # 3. Normalize Status
        raw_status = str(normalized.get("status", "")).upper()
        # Map common broker statuses to standard states
        status_map = {
            "COMPLETE": "FILLED",
            "COMPLETED": "FILLED",
            "DONE": "FILLED",
            "SUBMITTED": "PENDING",
            "NEW": "ACCEPTED",
            "OPEN": "PENDING",
            "TRIGGER_PENDING": "PENDING",
        }
        status = status_map.get(raw_status, raw_status)
        normalized["status"] = status

        # 4. Support valid states
        valid_statuses = {"ACCEPTED", "PENDING", "FILLED", "PARTIALLY_FILLED", "REJECTED", "CANCELLED", "FAILED"}
        if status not in valid_statuses:
            reason = f"Unrecognized broker order status '{status}' (raw: {raw_status})"
            logger.error(f"Execution Response Validation Failed: {reason}")
            normalized["status"] = "FAILED"
            normalized["reason"] = reason
            return False, reason, normalized

        # 5. Handle Terminal / Failure States
        if status in {"REJECTED", "CANCELLED", "FAILED"}:
            reason = normalized.get("reason") or f"Broker order execution failed with status {status}"
            logger.warning(f"Broker Order Execution Rejected/Failed: {reason}")
            normalized["reason"] = str(reason)
            # Cannot create OPEN position from invalid/rejected response
            return False, str(reason), normalized

        # 6. Handle Partial Fills explicitly (does not count as fully filled)
        if status == "PARTIALLY_FILLED":
            filled_qty = normalized.get("filled_quantity", 0)
            reason = f"Order {order_id} partially filled ({filled_qty} shares)"
            logger.info(f"Execution Response: {reason}")
            normalized["reason"] = reason
            return True, reason, normalized

        # 7. Success for ACCEPTED, PENDING, FILLED
        return True, f"Execution Response Validated ({status})", normalized

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
        strategy_id: Optional[str] = None,
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

        # Initialize Order Lifecycle Tracker (BETA-CODE-04)
        tracker = OrderLifecycleTracker(
            order_id=order_id,
            symbol=clean_symbol,
            transaction_type=transaction_type,
            quantity=norm_qty,
            price=norm_price,
            strategy_id=strategy_id,
        )
        await tracker.transition_to(OrderLifecycleState.RISK_APPROVED, reason="Risk check passed")
        await tracker.transition_to(OrderLifecycleState.ORDER_CREATED, reason="Order payload constructed")

        # BETA-CODE-01 & BETA-CODE-02: Mode & Enable Gate checks
        if self.mode == "LIVE":
            # BETA-CODE-02: Trading Enable Gate Check
            cfg = get_config().apply_override()
            if not cfg.v2_trading_enabled:
                reason = "Trading is disabled by configuration (v2_trading_enabled=false)"
                logger.error(f"LIVE Order Blocked at Execution Boundary: {reason}")
                await tracker.transition_to(OrderLifecycleState.REJECTED, reason=reason)
                return {
                    "order_id": None,
                    "status": "REJECTED",
                    "reason": reason,
                    "symbol": clean_symbol,
                    "timestamp": now.isoformat(),
                    "lifecycle": tracker.to_dict(),
                }

            # BETA-CODE-01: Fail-Safe LIVE Execution (NEVER fall through to paper!)
            if not self._connected:
                self.connect()

            if not self._connected or self.live_adapter is None:
                reason = "LIVE mode execution failed: Live broker adapter is unavailable or disconnected"
                logger.error(f"LIVE Order Execution Error: {reason}")
                await tracker.transition_to(OrderLifecycleState.FAILED, reason=reason)
                return {
                    "order_id": None,
                    "status": "FAILED",
                    "reason": reason,
                    "symbol": clean_symbol,
                    "timestamp": now.isoformat(),
                    "lifecycle": tracker.to_dict(),
                }

            # Dispatch order to live adapter
            try:
                await tracker.transition_to(OrderLifecycleState.SUBMITTED, reason="Dispatched to Live Broker")
                live_resp = await asyncio.to_thread(
                    self.live_adapter.place_order,
                    symbol=clean_symbol,
                    quantity=norm_qty,
                    side=transaction_type.upper(),
                    order_type=order_type.upper(),
                    price=norm_price,
                )
                valid, val_reason, val_resp = self.validate_execution_response(live_resp)
                if not valid:
                    await tracker.transition_to(OrderLifecycleState.FAILED, reason=val_reason)
                    val_resp["lifecycle"] = tracker.to_dict()
                    return val_resp

                await tracker.transition_to(OrderLifecycleState.FILLED, reason="Live Order Executed", broker_order_id=val_resp.get("order_id"))
                target_state = OrderLifecycleState.PARTIALLY_FILLED if val_resp.get("status") == "PARTIALLY_FILLED" else OrderLifecycleState.FILLED
                await tracker.transition_to(
                    target_state, 
                    reason="Live Order Executed", 
                    broker_order_id=val_resp.get("order_id"),
                    filled_quantity=val_resp.get("filled_quantity", 0)
                )
                val_resp["lifecycle"] = tracker.to_dict()
                return val_resp
            except Exception as e:
                reason = f"Live broker dispatch exception: {e}"
                logger.error(reason, exc_info=True)
                await tracker.transition_to(OrderLifecycleState.FAILED, reason=reason)
                return {
                    "order_id": None,
                    "status": "FAILED",
                    "reason": reason,
                    "symbol": clean_symbol,
                    "timestamp": now.isoformat(),
                    "lifecycle": tracker.to_dict(),
                }

        # Explicit PAPER Simulation Execution Mode
        await tracker.transition_to(OrderLifecycleState.SUBMITTED, reason="Simulated Paper Order Submitted")
        await tracker.transition_to(OrderLifecycleState.ACKNOWLEDGED, reason="Simulated Broker Acknowledged")

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

        await tracker.transition_to(OrderLifecycleState.FILLED, reason="Simulated Paper Order Filled")
        order_record["lifecycle"] = tracker.to_dict()

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
        if self.mode == "LIVE":
            if not self._connected:
                self.connect()
            if not self._connected or self.live_adapter is None:
                logger.error(f"LIVE mode execution failed: Cannot cancel order {order_id}, live broker adapter unavailable")
                return False
            try:
                if hasattr(self.live_adapter, "cancel_order"):
                    return await asyncio.to_thread(self.live_adapter.cancel_order, order_id)
                else:
                    logger.error(f"Live broker adapter does not support cancel_order.")
                    return False
            except Exception as e:
                logger.error(f"Live broker cancel exception: {e}", exc_info=True)
                return False

        if order_id in self._simulated_orders:
            self._simulated_orders[order_id]["status"] = "CANCELLED"
            logger.info(f"Cancelled order: {order_id}")
            return True
        return False

    async def get_ltp(self, symbol: str, exchange: Optional[str] = None) -> float:
        """Fetches live Last Traded Price (LTP) for a symbol."""
        if self.mode == "LIVE":
            if not self._connected:
                self.connect()
            if not self._connected or self.live_adapter is None:
                logger.error(f"LIVE mode execution failed: Cannot fetch LTP for {symbol}, live broker adapter unavailable")
                return 0.0
            try:
                if hasattr(self.live_adapter, "get_ltp"):
                    return await asyncio.to_thread(self.live_adapter.get_ltp, symbol, exchange)
                else:
                    logger.error("Live broker adapter does not support get_ltp.")
                    return 0.0
            except Exception as e:
                logger.error(f"Failed to fetch live LTP for {symbol}: {e}", exc_info=True)
                return 0.0

        # Simulated price lookup
        return 100.0

