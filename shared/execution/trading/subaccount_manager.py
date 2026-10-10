"""
CoinDCX Unified Capital Pool & Execution Manager Architecture.

Migrated from isolated sub-accounts to a single unified capital pool with
runtime-configured exposure controls and no hard-coded per-bot ceiling.
with standardized micro-order allocation (₹200 per trade), master HMAC-SHA256 request signing,
order book precision rounding, mandatory ₹100 minimum notional enforcement, and live REST order dispatch.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from core.logging import get_logger
from core.types import BotName

from .precision_rules import (
    get_pair_spec,
    normalize_price,
    normalize_qty,
    round_price,
    round_qty,
    validate_order_notional,
)

logger = get_logger("execution.trading.subaccount_manager")


@dataclass
class SubAccountConfig:
    bot_name: BotName
    subaccount_id: str
    api_key: str
    api_secret: str
    allocated_wallet_inr: float | None = None
    max_positions: int = 10
    default_trade_amount_inr: float = 200.0
    allowed_pairs: list[str] = field(default_factory=list)


class CoinDCXSubAccountClient:
    """
    Authenticated execution client managing orders under the Unified Capital Pool.
    Signs all authenticated endpoints using CoinDCX HMAC-SHA256 standard.
    """

    def __init__(
        self,
        config: SubAccountConfig,
        base_url: str = "https://api.coindcx.com",
        shared_pool_lock: threading.RLock | None = None,
        shared_state: dict[str, float] | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.config = config
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._lock = shared_pool_lock or threading.RLock()
        self._shared_state = shared_state
        if self._shared_state is None:
            self._shared_state = {
                "wallet_balance_inr": (
                    config.allocated_wallet_inr
                    if config.allocated_wallet_inr is not None
                    else float("inf")
                ),
                "deployed_capital_inr": 0.0,
            }
        self._open_orders: dict[str, dict[str, Any]] = {}

    @property
    def bot_name(self) -> BotName:
        return self.config.bot_name

    @property
    def subaccount_id(self) -> str:
        return self.config.subaccount_id

    @property
    def wallet_balance_inr(self) -> float:
        with self._lock:
            return self._shared_state["wallet_balance_inr"]

    @property
    def available_balance_inr(self) -> float:
        with self._lock:
            return max(
                0.0,
                self._shared_state["wallet_balance_inr"]
                - self._shared_state["deployed_capital_inr"],
            )

    @property
    def mode(self) -> str:
        return getattr(
            self.config, "mode", getattr(self.config, "deployment_mode", "PAPER")
        )

    @property
    def is_live_mode(self) -> bool:
        current_env = os.environ.get("DEPLOYMENT_MODE", self.mode).upper()
        return current_env in ("LIVE", "LIVE_MICROCASH")

    def generate_auth_headers(self, payload: dict[str, Any]) -> dict[str, str]:
        """
        Generate HMAC-SHA256 authentication headers for CoinDCX API.
        Headers:
          - X-AUTH-APIKEY: API Key
          - X-AUTH-SIGNATURE: HMAC-SHA256 hex digest of payload JSON
        """
        if "timestamp" not in payload:
            payload["timestamp"] = int(time.time() * 1000)

        api_key = (self.config.api_key or "").strip()
        api_secret = (self.config.api_secret or "").strip()

        json_body = json.dumps(payload, separators=(",", ":"))
        secret_bytes = api_secret.encode("utf-8")
        signature = hmac.new(
            secret_bytes, json_body.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        return {
            "Content-Type": "application/json",
            "X-AUTH-APIKEY": api_key,
            "X-AUTH-SIGNATURE": signature,
        }

    async def _post_exchange(
        self,
        endpoint: str,
        payload: dict[str, Any],
        client: httpx.AsyncClient | None = None,
    ) -> dict[str, Any]:
        """Execute authenticated async HTTP POST request to CoinDCX endpoint."""
        if not self.config.api_key or not self.config.api_secret:
            logger.error("CoinDCX API request failed on %s: missing API credentials", endpoint)
            return {
                "success": False,
                "error": "MISSING_API_CREDENTIALS",
                "message": "CoinDCX API credentials not configured.",
            }

        url = f"{self.base_url.rstrip('/')}/{endpoint.lstrip('/')}"
        headers = self.generate_auth_headers(payload)

        try:
            if client is not None:
                response = await client.post(url, headers=headers, json=payload)
                if response.status_code == 429:
                    logger.warning("CoinDCX rate-limit (429) on %s", endpoint)
                    return {
                        "success": False,
                        "error": "RATE_LIMITED",
                        "status_code": 429,
                        "message": "CoinDCX API rate limit reached.",
                    }
                response.raise_for_status()
                data = response.json()
                return {"success": True, "data": data}
            else:
                async with httpx.AsyncClient(timeout=self.timeout) as http_client:
                    response = await http_client.post(
                        url, headers=headers, json=payload
                    )
                    if response.status_code == 429:
                        logger.warning("CoinDCX rate-limit (429) on %s", endpoint)
                        return {
                            "success": False,
                            "error": "RATE_LIMITED",
                            "status_code": 429,
                            "message": "CoinDCX API rate limit reached.",
                        }
                    response.raise_for_status()
                    data = response.json()
                    return {"success": True, "data": data}
        except httpx.TimeoutException:
            logger.error("CoinDCX API timeout (%.1fs) on %s", self.timeout, endpoint)
            return {
                "success": False,
                "error": "TIMEOUT",
                "message": f"CoinDCX API timed out after {self.timeout}s.",
            }
        except httpx.HTTPStatusError as e:
            logger.error(
                "CoinDCX API HTTP error %s on %s: %s",
                e.response.status_code,
                endpoint,
                e.response.text,
            )
            return {
                "success": False,
                "error": f"HTTP_{e.response.status_code}",
                "status_code": e.response.status_code,
                "message": str(e),
            }
        except Exception as e:
            logger.error("CoinDCX API connection error on %s: %s", endpoint, e)
            return {
                "success": False,
                "error": "CONNECTION_ERROR",
                "message": str(e),
            }


    async def place_order_async(
        self,
        pair: str,
        side: str,
        price: float,
        qty: float = 0.0,
        order_type: str = "limit_order",
        client_order_id: str | None = None,
        stop_price: float | None = None,
        total_quantity: float | None = None,
    ) -> dict[str, Any]:
        """
        Asynchronously place an order. Dispatches real HTTP request if in LIVE_MICROCASH mode,
        otherwise records a compliant simulated fill.
        """
        if total_quantity is not None:
            qty = total_quantity

        with self._lock:
            try:
                norm_price = normalize_price(price)
                norm_qty = normalize_qty(qty)
                norm_stop_px = (
                    normalize_price(stop_price) if stop_price is not None else None
                )
            except ValueError as e:
                logger.warning("Order rejected: invalid price/qty for %s: %s", pair, e)
                return {
                    "success": False,
                    "error": "INVALID_PRICE_OR_QUANTITY",
                    "message": f"Order rejected: {e}",
                }

            rounded_price = round_price(pair, norm_price)
            rounded_qty = round_qty(pair, norm_qty)
            rounded_stop_px = (
                round_price(pair, norm_stop_px) if norm_stop_px is not None else None
            )
            notional = rounded_price * rounded_qty

            if not validate_order_notional(pair, rounded_price, rounded_qty):
                spec = get_pair_spec(pair, reference_price=rounded_price)
                return {
                    "success": False,
                    "error": "ORDER_NOTIONAL_BELOW_MINIMUM",
                    "message": f"Order notional INR {notional:.2f} is below minimum INR {spec.min_notional_inr:.2f} or invalid lot size",
                }

            if side.upper() == "BUY" and notional > self.available_balance_inr:
                return {
                    "success": False,
                    "error": "INSUFFICIENT_SUBACCOUNT_BALANCE",
                    "message": f"Required INR {notional:.2f} exceeds available capital pool balance INR {self.available_balance_inr:.2f}",
                }

            order_id = (
                client_order_id or f"ORD_{self.subaccount_id}_{int(time.time() * 1000)}"
            )
            ex_id = f"CDX_{self.subaccount_id}_{int(time.time() * 1000)}"
            payload = {
                "side": side.lower(),
                "order_type": order_type,
                "market": pair.replace("/", "").upper(),
                "price_per_unit": rounded_price,
                "total_quantity": rounded_qty,
                "timestamp": int(time.time() * 1000),
                "client_order_id": order_id,
            }
            if rounded_stop_px is not None:
                payload["stop_price"] = rounded_stop_px

            headers = self.generate_auth_headers(payload)

            if side.upper() == "BUY":
                self._shared_state["deployed_capital_inr"] += notional

            order_record = {
                "order_id": order_id,
                "exchange_order_id": ex_id,
                "subaccount_id": self.subaccount_id,
                "bot_name": self.bot_name.value,
                "pair": pair,
                "side": side.upper(),
                "price": rounded_price,
                "qty": rounded_qty,
                "notional_inr": notional,
                "status": "OPEN" if order_type.lower() == "stop_limit" else "FILLED",
                "auth_headers_verified": bool(headers.get("X-AUTH-SIGNATURE")),
                "timestamp": payload["timestamp"],
            }
            if rounded_stop_px is not None:
                order_record["stop_price"] = rounded_stop_px

        # If live mode, execute outbound HTTP request
        if self.is_live_mode:
            http_res = await self._post_exchange("exchange/v1/orders/create", payload)
            if not http_res.get("success"):
                with self._lock:
                    if side.upper() == "BUY":
                        self._shared_state["deployed_capital_inr"] = max(
                            0.0, self._shared_state["deployed_capital_inr"] - notional
                        )
                return http_res
            order_record["exchange_response"] = http_res.get("data")
            order_record["live_dispatched"] = True
            live_ex_id = http_res.get("data", {}).get("id") or http_res.get(
                "data", {}
            ).get("order_id")
            if live_ex_id:
                order_record["exchange_order_id"] = str(live_ex_id)

        with self._lock:
            self._open_orders[order_id] = order_record

        logger.info(
            "[%s] Order dispatched successfully (live=%s): %s %s @ INR %.8f (Qty: %s, Notional: INR %.2f)",
            self.subaccount_id,
            self.is_live_mode,
            side.upper(),
            pair,
            rounded_price,
            rounded_qty,
            notional,
        )
        return {
            "success": True,
            "exchange_order_id": order_record["exchange_order_id"],
            "order": order_record,
        }

    # ── Synchronous / Simulated Order Placement ───────────────────────────────

    def place_order(
        self,
        pair: str,
        side: str,
        price: float,
        qty: float = 0.0,
        order_type: str = "limit_order",
        client_order_id: str | None = None,
        stop_price: float | None = None,
        total_quantity: float | None = None,
    ) -> dict[str, Any]:
        """
        Place an order through the unified execution client with discrete rounding.
        """
        if total_quantity is not None:
            qty = total_quantity

        with self._lock:
            try:
                norm_price = normalize_price(price)
                norm_qty = normalize_qty(qty)
                norm_stop_px = (
                    normalize_price(stop_price) if stop_price is not None else None
                )
            except ValueError as e:
                logger.warning("Order rejected: invalid price/qty for %s: %s", pair, e)
                return {
                    "success": False,
                    "error": "INVALID_PRICE_OR_QUANTITY",
                    "message": f"Order rejected: {e}",
                }

            # 1. Discrete tick & lot precision rounding
            rounded_price = round_price(pair, norm_price)
            rounded_qty = round_qty(pair, norm_qty)
            rounded_stop_px = (
                round_price(pair, norm_stop_px) if norm_stop_px is not None else None
            )
            notional = rounded_price * rounded_qty

            # 2. Hard validation: Minimum order value and min lot via CoinDCX precision rules
            if not validate_order_notional(pair, rounded_price, rounded_qty):
                spec = get_pair_spec(pair, reference_price=rounded_price)
                logger.warning(
                    "Order rejected by precision gate: notional INR %.2f below INR %.2f or invalid qty %.6f for %s",
                    notional,
                    spec.min_notional_inr,
                    rounded_qty,
                    pair,
                )
                return {
                    "success": False,
                    "error": "ORDER_NOTIONAL_BELOW_MINIMUM",
                    "message": f"Order notional INR {notional:.2f} is below minimum INR {spec.min_notional_inr:.2f} or invalid lot size",
                }

            # 3. Capital pool balance check
            if side.upper() == "BUY" and notional > self.available_balance_inr:
                logger.warning(
                    "Order rejected: Insufficient capital pool funds (Required INR %.2f > Available INR %.2f)",
                    notional,
                    self.available_balance_inr,
                )
                return {
                    "success": False,
                    "error": "INSUFFICIENT_SUBACCOUNT_BALANCE",
                    "message": f"Required INR {notional:.2f} exceeds available capital pool balance INR {self.available_balance_inr:.2f}",
                }

            # 4. Generate payload and client order ID
            order_id = (
                client_order_id or f"ORD_{self.subaccount_id}_{int(time.time() * 1000)}"
            )
            ex_id = (
                f"CDX_SL_{int(time.time() * 1000)}"
                if order_type.lower() == "stop_limit"
                else f"CDX_{int(time.time() * 1000)}"
            )
            payload = {
                "side": side.lower(),
                "order_type": order_type,
                "market": pair.replace("/", "").upper(),
                "price_per_unit": rounded_price,
                "total_quantity": rounded_qty,
                "timestamp": int(time.time() * 1000),
                "client_order_id": order_id,
            }
            if rounded_stop_px is not None:
                payload["stop_price"] = rounded_stop_px

            headers = self.generate_auth_headers(payload)

            # Update shared capital tracking
            if side.upper() == "BUY":
                self._shared_state["deployed_capital_inr"] += notional

            order_record = {
                "order_id": order_id,
                "exchange_order_id": ex_id,
                "subaccount_id": self.subaccount_id,
                "bot_name": self.bot_name.value,
                "pair": pair,
                "side": side.upper(),
                "price": rounded_price,
                "qty": rounded_qty,
                "notional_inr": notional,
                "status": "OPEN" if order_type.lower() == "stop_limit" else "FILLED",
                "auth_headers_verified": bool(headers.get("X-AUTH-SIGNATURE")),
                "timestamp": payload["timestamp"],
            }
            if rounded_stop_px is not None:
                order_record["stop_price"] = rounded_stop_px
            self._open_orders[order_id] = order_record

            logger.info(
                "[%s] Order dispatched successfully (live=%s): %s %s @ INR %.2f (Qty: %s, Notional: INR %.2f)",
                self.subaccount_id,
                self.is_live_mode,
                side.upper(),
                pair,
                rounded_price,
                rounded_qty,
                notional,
            )
            return {
                "success": True,
                "exchange_order_id": order_record["exchange_order_id"],
                "order": order_record,
            }


    async def get_account_balances(self) -> dict[str, Any]:
        """Fetch account balances from CoinDCX."""
        payload = {"timestamp": int(time.time() * 1000)}
        if self.is_live_mode:
            return await self._post_exchange("exchange/v1/users/balances", payload)
        with self._lock:
            wb = self.wallet_balance_inr
            ab = self.available_balance_inr
            return {
                "success": True,
                "wallet_balance_inr": None if (wb is None or math.isinf(wb)) else wb,
                "available_balance_inr": None if (ab is None or math.isinf(ab)) else ab,
                "deployed_capital_inr": self._deployed_capital_inr,
            }

    def close_position_fill(
        self, notional_returned: float, realized_pnl: float
    ) -> None:
        """Update capital pool balance when a position closes."""
        with self._lock:
            self._shared_state["deployed_capital_inr"] = max(
                0.0, self._shared_state["deployed_capital_inr"] - notional_returned
            )
            self._shared_state["wallet_balance_inr"] += realized_pnl

    # ── Live Network Execution & Diagnostic REST Endpoints ────────────────────

    async def get_balances(
        self,
        client: httpx.AsyncClient | None = None,
    ) -> dict[str, Any]:
        """
        Query real CoinDCX user balances endpoint:
        POST https://api.coindcx.com/exchange/v1/users/balances
        """
        if not self.config.api_key or not self.config.api_secret:
            logger.error(
                "[%s] CoinDCX balance fetch failed: missing API credentials",
                self.subaccount_id,
            )
            return {
                "success": False,
                "error": "MISSING_API_CREDENTIALS",
                "message": "CoinDCX API credentials not configured.",
            }

        payload = {"timestamp": int(time.time() * 1000)}
        headers = self.generate_auth_headers(payload)
        url = f"{self.base_url}/exchange/v1/users/balances"

        owns_client = client is None
        http = client or httpx.AsyncClient(timeout=self.timeout)
        try:
            resp = await http.post(url, json=payload, headers=headers)
            if resp.status_code == 200:
                balances_raw = resp.json()
                inr_balance = 0.0
                inr_locked = 0.0
                asset_balances: dict[str, float] = {}
                if isinstance(balances_raw, list):
                    for b in balances_raw:
                        curr = str(b.get("currency", "")).upper()
                        bal = float(b.get("balance", 0.0))
                        locked = float(b.get("locked_balance", 0.0))
                        if curr == "INR":
                            inr_balance = bal
                            inr_locked = locked
                        elif curr:
                            asset_balances[curr] = bal + locked

                return {
                    "success": True,
                    "status_code": 200,
                    "inr_balance": inr_balance,
                    "inr_locked": inr_locked,
                    "asset_balances": asset_balances,
                    "balances": balances_raw,
                }
            elif resp.status_code == 401:
                logger.error(
                    "[%s] CoinDCX Authentication Failed (HTTP 401): %s",
                    self.subaccount_id,
                    resp.text,
                )
                return {
                    "success": False,
                    "status_code": 401,
                    "error": "AUTH_FAILED",
                    "message": "Invalid API Key or HMAC Signature",
                    "details": resp.text,
                }
            elif resp.status_code == 429:
                logger.warning(
                    "[%s] CoinDCX Rate Limit Exceeded (HTTP 429)", self.subaccount_id
                )
                return {
                    "success": False,
                    "status_code": 429,
                    "error": "RATE_LIMIT_EXCEEDED",
                    "message": "Rate limit exceeded (HTTP 429)",
                }
            else:
                logger.error(
                    "[%s] Balance fetch error (HTTP %d): %s",
                    self.subaccount_id,
                    resp.status_code,
                    resp.text,
                )
                return {
                    "success": False,
                    "status_code": resp.status_code,
                    "error": "EXCHANGE_ERROR",
                    "message": f"CoinDCX returned HTTP {resp.status_code}",
                    "details": resp.text,
                }
        except Exception as e:
            logger.error(
                "[%s] Error connecting to CoinDCX balance API: %s",
                self.subaccount_id,
                e,
            )
            return {
                "success": False,
                "status_code": 0,
                "error": "NETWORK_ERROR",
                "message": str(e),
            }
        finally:
            if owns_client:
                await http.aclose()

    async def place_live_order(
        self,
        pair: str,
        side: str,
        price: float,
        qty: float,
        order_type: str = "limit_order",
        client_order_id: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> dict[str, Any]:
        """
        Dispatch a live authenticated order to CoinDCX:
        POST https://api.coindcx.com/exchange/v1/orders/create
        """
        order_id = (
            client_order_id or f"ORD_{self.subaccount_id}_{int(time.time() * 1000)}"
        )

        # 0. Global Kill-Switch & Execution Mode Isolation Gate
        from core.config import get_config

        cfg = get_config()
        mode = getattr(cfg, "deployment_mode", "PAPER").upper()
        if not cfg.trading_enabled or mode not in ("LIVE", "LIVE_MICROCASH"):
            logger.warning(
                "[%s] Outbound live order blocked by kill-switch/mode gate: trading_enabled=%s, mode=%s",
                self.subaccount_id,
                cfg.trading_enabled,
                mode,
            )
            return {
                "success": False,
                "error": "EXECUTION_BLOCKED_KILL_SWITCH",
                "message": f"Live order blocked: trading_enabled={cfg.trading_enabled}, mode={mode}",
                "client_order_id": order_id,
            }

        # 1. Round price and qty
        rounded_price = round_price(pair, price)
        rounded_qty = round_qty(pair, qty)
        notional = rounded_price * rounded_qty

        # 2. Hard validation: Minimum order value and min lot via CoinDCX precision rules
        spec = get_pair_spec(pair)
        if not validate_order_notional(pair, rounded_price, rounded_qty):
            return {
                "success": False,
                "error": "ORDER_NOTIONAL_BELOW_MINIMUM",
                "message": f"Order notional INR {notional:.2f} is below CoinDCX minimum INR {spec.min_notional_inr:.2f} or min lot {spec.min_lot_qty}",
                "client_order_id": order_id,
            }

        owns_client = client is None
        http = client or httpx.AsyncClient(timeout=self.timeout)
        try:
            # 3. Dynamic Balance Verification in LIVE mode
            if side.upper() == "BUY":
                bal_res = await self.get_balances(client=http)
                if not bal_res.get("success"):
                    logger.error(
                        "[%s] Could not obtain CoinDCX balance for live order: %s",
                        self.subaccount_id,
                        bal_res.get("error"),
                    )
                    return {
                        "success": False,
                        "error": "BLOCKED_BALANCE_UNAVAILABLE",
                        "message": "CoinDCX live balance could not be obtained or verified (capital unknown). Failing closed.",
                        "client_order_id": order_id,
                    }
                live_inr = float(bal_res.get("inr_balance", 0.0))
                with self._lock:
                    self._shared_state["wallet_balance_inr"] = live_inr

                if notional > live_inr:
                    return {
                        "success": False,
                        "error": "INSUFFICIENT_SUBACCOUNT_BALANCE",
                        "message": f"Required INR {notional:.2f} exceeds CoinDCX live available balance INR {live_inr:.2f}",
                        "client_order_id": order_id,
                    }

            payload = {
                "side": side.lower(),
                "order_type": order_type,
                "market": pair.replace("/", "").upper(),
                "price_per_unit": rounded_price,
                "total_quantity": rounded_qty,
                "timestamp": int(time.time() * 1000),
                "client_order_id": order_id,
            }

            headers = self.generate_auth_headers(payload)
            url = f"{self.base_url}/exchange/v1/orders/create"
            resp = await http.post(url, json=payload, headers=headers)
            if resp.status_code in (200, 201):
                data = resp.json()
                raw_order: dict[str, Any] = {}
                if isinstance(data, dict):
                    if (
                        "orders" in data
                        and isinstance(data["orders"], list)
                        and len(data["orders"]) > 0
                    ):
                        raw_order = data["orders"][0]
                    else:
                        raw_order = data
                elif isinstance(data, list) and len(data) > 0:
                    raw_order = data[0] if isinstance(data[0], dict) else {}

                exchange_order_id = str(
                    raw_order.get("id") or raw_order.get("order_id") or ""
                )
                raw_status = str(raw_order.get("status", "open")).upper()

                # If status is open/pending, perform a fast follow-up status check
                if (
                    raw_status not in ("FILLED", "REJECTED", "CANCELLED")
                    and exchange_order_id
                ):
                    try:
                        await asyncio.sleep(0.1)
                        status_resp = await self.get_order_status(
                            exchange_order_id, client=http
                        )
                        if status_resp.get("success"):
                            st_order = status_resp.get("order")
                            if isinstance(st_order, dict):
                                raw_status = str(
                                    st_order.get("status", raw_status)
                                ).upper()
                    except Exception:
                        pass

                is_filled = raw_status == "FILLED"
                actual_filled_qty = float(
                    raw_order.get("filled_quantity")
                    or raw_order.get("filled_qty")
                    or (rounded_qty if is_filled else 0.0)
                )
                fill_price = float(
                    raw_order.get("price_per_unit")
                    or raw_order.get("price")
                    or rounded_price
                )

                with self._lock:
                    if side.upper() == "BUY" and is_filled:
                        self._shared_state["deployed_capital_inr"] += notional

                return {
                    "success": True,
                    "status_code": resp.status_code,
                    "order": raw_order,
                    "exchange_order_id": exchange_order_id or None,
                    "client_order_id": order_id,
                    "status": raw_status,
                    "is_filled": is_filled,
                    "filled_qty": actual_filled_qty,
                    "price": fill_price,
                    "qty": rounded_qty,
                    "notional_inr": notional,
                }
            elif resp.status_code == 401:
                return {
                    "success": False,
                    "status_code": 401,
                    "error": "AUTH_FAILED",
                    "message": "Invalid API Key or HMAC Signature",
                    "client_order_id": order_id,
                }
            elif resp.status_code == 429:
                return {
                    "success": False,
                    "status_code": 429,
                    "error": "RATE_LIMIT_EXCEEDED",
                    "message": "Rate limit exceeded",
                    "client_order_id": order_id,
                }
            else:
                return {
                    "success": False,
                    "status_code": resp.status_code,
                    "error": "ORDER_REJECTED",
                    "message": f"CoinDCX returned HTTP {resp.status_code}",
                    "details": resp.text,
                    "client_order_id": order_id,
                }
        except httpx.TimeoutException as te:
            logger.warning(
                "[%s] Timeout placing order on CoinDCX for %s: %s",
                self.subaccount_id,
                pair,
                te,
            )
            return {
                "success": False,
                "status_code": 408,
                "error": "TIMEOUT",
                "message": f"Timeout contacting CoinDCX: {te}",
                "client_order_id": order_id,
                "requires_reconciliation": True,
            }
        except Exception as e:
            return {
                "success": False,
                "status_code": 0,
                "error": "NETWORK_ERROR",
                "message": str(e),
                "client_order_id": order_id,
            }
        finally:
            if owns_client:
                await http.aclose()

    async def get_order_status(
        self,
        order_id: str,
        client: httpx.AsyncClient | None = None,
    ) -> dict[str, Any]:
        """
        Query status of an active or completed order:
        POST https://api.coindcx.com/exchange/v1/orders/status
        Normalizes response across CoinDCX order payload shapes.
        """
        if not self.is_live_mode and client is None:
            with self._lock:
                ord_rec = self._open_orders.get(order_id)
                if not ord_rec:
                    for k, v in self._open_orders.items():
                        if str(v.get("exchange_order_id")) == str(order_id):
                            ord_rec = v
                            break
                if ord_rec:
                    st = str(ord_rec.get("status", "OPEN")).upper()
                    is_filled = st == "FILLED"
                    q = float(ord_rec.get("qty", 0.0))
                    p = float(ord_rec.get("price", 0.0))
                    filled_q = float(ord_rec.get("filled_qty", q if is_filled else 0.0))
                    return {
                        "success": True,
                        "status_code": 200,
                        "exchange_order_id": str(
                            ord_rec.get("exchange_order_id", order_id)
                        ),
                        "client_order_id": ord_rec.get("order_id"),
                        "status": st,
                        "is_filled": is_filled,
                        "filled_qty": filled_q,
                        "price": p,
                        "qty": q,
                        "order": ord_rec,
                        "error": None,
                        "message": None,
                    }
            return {
                "success": False,
                "status_code": 404,
                "exchange_order_id": order_id,
                "status": "ORDER_NOT_FOUND",
                "is_filled": False,
                "filled_qty": 0.0,
                "price": 0.0,
                "qty": 0.0,
                "order": {},
                "error": "ORDER_NOT_FOUND",
                "message": "Order not found in simulated open orders",
            }

        payload = {"id": order_id, "timestamp": int(time.time() * 1000)}
        headers = self.generate_auth_headers(payload)
        url = f"{self.base_url}/exchange/v1/orders/status"

        owns_client = client is None
        http = client or httpx.AsyncClient(timeout=self.timeout)
        try:
            resp = await http.post(url, json=payload, headers=headers)
            if resp.status_code == 200:
                raw_data = resp.json()
                raw_order: dict[str, Any] = {}
                if isinstance(raw_data, dict):
                    raw_order = raw_data
                elif (
                    isinstance(raw_data, list)
                    and len(raw_data) > 0
                    and isinstance(raw_data[0], dict)
                ):
                    raw_order = raw_data[0]

                ex_id = str(
                    raw_order.get("id") or raw_order.get("order_id") or order_id
                )
                cl_id = str(raw_order.get("client_order_id") or "")
                raw_status = str(raw_order.get("status", "UNKNOWN")).upper()
                is_filled = raw_status == "FILLED"
                p = float(
                    raw_order.get("price_per_unit") or raw_order.get("price") or 0.0
                )
                q = float(
                    raw_order.get("total_quantity") or raw_order.get("quantity") or 0.0
                )
                filled_q = float(
                    raw_order.get("filled_quantity")
                    or raw_order.get("filled_qty")
                    or (q if is_filled else 0.0)
                )

                return {
                    "success": True,
                    "status_code": 200,
                    "exchange_order_id": ex_id,
                    "client_order_id": cl_id or None,
                    "status": raw_status,
                    "is_filled": is_filled,
                    "filled_qty": filled_q,
                    "price": p,
                    "qty": q,
                    "order": raw_order,
                    "error": None,
                    "message": None,
                }
            return {
                "success": False,
                "status_code": resp.status_code,
                "exchange_order_id": order_id,
                "status": "FETCH_FAILED",
                "is_filled": False,
                "filled_qty": 0.0,
                "price": 0.0,
                "qty": 0.0,
                "order": {},
                "error": "FETCH_FAILED",
                "message": f"CoinDCX returned HTTP {resp.status_code}",
                "details": resp.text,
            }
        except Exception as e:
            return {
                "success": False,
                "status_code": 0,
                "exchange_order_id": order_id,
                "status": "NETWORK_ERROR",
                "is_filled": False,
                "filled_qty": 0.0,
                "price": 0.0,
                "qty": 0.0,
                "order": {},
                "error": "NETWORK_ERROR",
                "message": str(e),
            }
        finally:
            if owns_client:
                await http.aclose()

    async def get_order_by_client_id(
        self,
        client_order_id: str,
        client: httpx.AsyncClient | None = None,
    ) -> dict[str, Any]:
        """
        Query order status by client_order_id.
        Queries active_orders or status endpoint to reconcile timeout ambiguity.
        """
        payload = {
            "client_order_id": client_order_id,
            "timestamp": int(time.time() * 1000),
        }
        headers = self.generate_auth_headers(payload)
        url = f"{self.base_url}/exchange/v1/orders/status"

        owns_client = client is None
        http = client or httpx.AsyncClient(timeout=self.timeout)
        try:
            resp = await http.post(url, json=payload, headers=headers)
            if resp.status_code == 200:
                raw_data = resp.json()
                raw_order: dict[str, Any] = {}
                if isinstance(raw_data, dict):
                    raw_order = raw_data
                elif (
                    isinstance(raw_data, list)
                    and len(raw_data) > 0
                    and isinstance(raw_data[0], dict)
                ):
                    raw_order = raw_data[0]

                ex_id = str(raw_order.get("id") or raw_order.get("order_id") or "")
                raw_status = str(raw_order.get("status", "UNKNOWN")).upper()
                is_filled = raw_status == "FILLED"
                p = float(
                    raw_order.get("price_per_unit") or raw_order.get("price") or 0.0
                )
                q = float(
                    raw_order.get("total_quantity") or raw_order.get("quantity") or 0.0
                )
                filled_q = float(
                    raw_order.get("filled_quantity")
                    or raw_order.get("filled_qty")
                    or (q if is_filled else 0.0)
                )

                return {
                    "success": True,
                    "status_code": 200,
                    "exchange_order_id": ex_id or None,
                    "client_order_id": client_order_id,
                    "status": raw_status,
                    "is_filled": is_filled,
                    "filled_qty": filled_q,
                    "price": p,
                    "qty": q,
                    "order": raw_order,
                    "error": None,
                    "message": None,
                }
            return {
                "success": False,
                "status_code": resp.status_code,
                "client_order_id": client_order_id,
                "exchange_order_id": None,
                "status": "NOT_FOUND",
                "is_filled": False,
                "filled_qty": 0.0,
                "price": 0.0,
                "qty": 0.0,
                "order": {},
                "error": "NOT_FOUND",
                "message": f"Order with client_order_id {client_order_id} not found on exchange",
            }
        except Exception as e:
            return {
                "success": False,
                "status_code": 0,
                "client_order_id": client_order_id,
                "exchange_order_id": None,
                "status": "NETWORK_ERROR",
                "is_filled": False,
                "filled_qty": 0.0,
                "price": 0.0,
                "qty": 0.0,
                "order": {},
                "error": "NETWORK_ERROR",
                "message": str(e),
            }
        finally:
            if owns_client:
                await http.aclose()

    async def cancel_order(
        self,
        order_id: str,
        client: httpx.AsyncClient | None = None,
    ) -> dict[str, Any]:
        """
        Cancel an open limit order:
        POST https://api.coindcx.com/exchange/v1/orders/cancel
        """
        if not self.is_live_mode:
            with self._lock:
                if order_id in self._open_orders:
                    self._open_orders[order_id]["status"] = "CANCELLED"
                else:
                    for k, v in self._open_orders.items():
                        if str(v.get("exchange_order_id")) == str(order_id):
                            v["status"] = "CANCELLED"
                            break
            return {
                "success": True,
                "status_code": 200,
                "result": {"status": "cancelled", "id": order_id},
            }

        payload = {"id": order_id, "timestamp": int(time.time() * 1000)}
        headers = self.generate_auth_headers(payload)
        url = f"{self.base_url}/exchange/v1/orders/cancel"

        owns_client = client is None
        http = client or httpx.AsyncClient(timeout=self.timeout)
        try:
            resp = await http.post(url, json=payload, headers=headers)
            if resp.status_code == 200:
                return {"success": True, "status_code": 200, "result": resp.json()}
            return {
                "success": False,
                "status_code": resp.status_code,
                "error": "CANCEL_FAILED",
                "details": resp.text,
            }
        except Exception as e:
            return {
                "success": False,
                "status_code": 0,
                "error": "NETWORK_ERROR",
                "message": str(e),
            }
        finally:
            if owns_client:
                await http.aclose()

    async def get_active_orders(
        self,
        market: str | None = None,
        side: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> dict[str, Any]:
        """
        Fetch active open orders from CoinDCX:
        POST https://api.coindcx.com/exchange/v1/orders/active_orders
        """
        payload: dict[str, Any] = {"timestamp": int(time.time() * 1000)}
        if market:
            payload["market"] = market.replace("/", "").upper()
        if side:
            payload["side"] = side.lower()

        if not self.is_live_mode:
            with self._lock:
                orders_list = [
                    rec
                    for rec in self._open_orders.values()
                    if rec.get("status")
                    in ("OPEN", "PARTIALLY_FILLED", "open", "partially_filled")
                ]
                return {
                    "success": True,
                    "orders": orders_list,
                    "count": len(orders_list),
                }

        headers = self.generate_auth_headers(payload)
        url = f"{self.base_url}/exchange/v1/orders/active_orders"

        owns_client = client is None
        http = client or httpx.AsyncClient(timeout=self.timeout)
        try:
            resp = await http.post(url, json=payload, headers=headers)
            if resp.status_code == 200:
                raw_data = resp.json()
                orders = (
                    raw_data.get("orders", raw_data)
                    if isinstance(raw_data, dict)
                    else raw_data
                )
                if not isinstance(orders, list):
                    orders = []
                return {
                    "success": True,
                    "status_code": 200,
                    "orders": orders,
                    "count": len(orders),
                }
            return {
                "success": False,
                "status_code": resp.status_code,
                "orders": [],
                "count": 0,
                "error": "FETCH_FAILED",
                "details": resp.text,
            }
        except Exception as e:
            logger.error(
                "[%s] Error fetching active orders from CoinDCX: %s",
                self.subaccount_id,
                e,
            )
            return {
                "success": False,
                "status_code": 0,
                "orders": [],
                "count": 0,
                "error": "NETWORK_ERROR",
                "message": str(e),
            }
        finally:
            if owns_client:
                await http.aclose()


# Aliases for unified architecture
CoinDCXExecutionClient = CoinDCXSubAccountClient


class CoinDCXSubAccountManager:
    """
    Thread-safe Unified Capital Pool Execution Manager & Multi-Strategy Router.
    All strategy bots share a unified capital pool with dynamic balance resolution.
    """

    def __init__(
        self, config_path: str = "Alpha/config.json", config: Any | None = None
    ) -> None:
        from core.config import get_config

        self.config_path = config_path
        self._explicit_config_passed = config is not None
        self._config = config if config is not None else get_config()
        self._clients: dict[BotName, CoinDCXSubAccountClient] = {}
        self._lock = threading.RLock()
        if self._explicit_config_passed:
            initial_balance = (
                float(self._config.total_capital_limit)
                if (self._config and self._config.total_capital_limit is not None)
                else math.inf
            )
        else:
            initial_balance = (
                float(self._config.total_capital_limit)
                if (self._config and self._config.total_capital_limit is not None)
                else 10000.0
            )
        self._shared_pool_state: dict[str, float] = {
            "wallet_balance_inr": initial_balance,
            "deployed_capital_inr": 0.0,
        }
        self._initialize_execution_pool()

    def _initialize_execution_pool(self) -> None:
        """Load configuration from AppConfig or config_override.json with safe fallbacks."""
        sub_configs: dict[str, dict[str, Any]] = {}
        order_size = float(self._config.order_size_inr) if self._config else 200.0
        if getattr(self, "_explicit_config_passed", False):
            pool_limit = (
                float(self._config.total_capital_limit)
                if (self._config and self._config.total_capital_limit is not None)
                else math.inf
            )
        else:
            pool_limit = (
                float(self._config.total_capital_limit)
                if (self._config and self._config.total_capital_limit is not None)
                else 10000.0
            )

        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    trading_cfg = data.get("trading", {})
                    sub_configs = data.get("subaccounts", data.get("strategies", {}))
                    if self._config is None:
                        if (
                            "capital_pool" in trading_cfg
                            or "total_portfolio_capital_inr" in data
                        ):
                            raw_pool = trading_cfg.get(
                                "capital_pool", data.get("total_portfolio_capital_inr")
                            )
                            if raw_pool is not None:
                                pool_limit = float(raw_pool)
                        if "order_size_inr" in trading_cfg:
                            order_size = float(trading_cfg["order_size_inr"])
            except Exception as e:
                logger.error("Failed to read %s: %s", self.config_path, e)

        self._shared_pool_state["wallet_balance_inr"] = pool_limit

        # Master API Credentials resolved with strict fallback hierarchy:
        # Key: 1. self._config.coindcx_live_api_key -> 2. self._config.coindcx_api_key -> 3. COINDCX_LIVE_API_KEY -> 4. COINDCX_API_KEY
        master_api_key = ""
        if self._config:
            if getattr(self._config, "coindcx_live_api_key", None):
                master_api_key = str(self._config.coindcx_live_api_key).strip()
            elif getattr(self._config, "coindcx_api_key", None):
                master_api_key = str(self._config.coindcx_api_key).strip()
        if not master_api_key:
            master_api_key = (os.getenv("COINDCX_LIVE_API_KEY") or os.getenv("COINDCX_API_KEY") or "").strip()

        # Secret: 1. self._config.coindcx_live_api_secret -> 2. self._config.coindcx_api_secret -> 3. COINDCX_LIVE_API_SECRET -> 4. COINDCX_API_SECRET
        master_api_secret = ""
        if self._config:
            if getattr(self._config, "coindcx_live_api_secret", None):
                master_api_secret = str(self._config.coindcx_live_api_secret).strip()
            elif getattr(self._config, "coindcx_api_secret", None):
                master_api_secret = str(self._config.coindcx_api_secret).strip()
        if not master_api_secret:
            master_api_secret = (os.getenv("COINDCX_LIVE_API_SECRET") or os.getenv("COINDCX_API_SECRET") or "").strip()

        # Default strategy configurations
        defaults = {
            "STE": {
                "subaccount_id": "ALPHA_STE_01",
                "max_positions": 10,
                "default_trade_amount_inr": order_size,
                "allowed_pairs": [
                    "BTC/INR",
                    "ETH/INR",
                    "SOL/INR",
                    "AVAX/INR",
                    "LINK/INR",
                    "BNB/INR",
                ],
            },
            "HDA": {
                "subaccount_id": "ALPHA_HDA_01",
                "max_positions": 10,
                "default_trade_amount_inr": order_size,
                "allowed_pairs": [
                    "BTC/INR",
                    "ETH/INR",
                    "SOL/INR",
                    "MATIC/INR",
                    "XRP/INR",
                    "ADA/INR",
                ],
            },
            "VCP": {
                "subaccount_id": "ALPHA_VCP_01",
                "max_positions": 10,
                "default_trade_amount_inr": order_size,
                "allowed_pairs": [
                    "SOL/INR",
                    "AVAX/INR",
                    "LINK/INR",
                    "ADA/INR",
                    "MATIC/INR",
                ],
            },
            "BBS": {
                "subaccount_id": "ALPHA_BBS_01",
                "max_positions": 10,
                "default_trade_amount_inr": order_size,
                "allowed_pairs": [
                    "BTC/INR",
                    "ETH/INR",
                    "SOL/INR",
                    "DOGE/INR",
                    "TRX/INR",
                    "SHIB/INR",
                ],
            },
        }

        for bot_key, bot_name in [
            ("STE", BotName.STE),
            ("HDA", BotName.HDA),
            ("VCP", BotName.VCP),
            ("BBS", BotName.BBS),
        ]:
            cfg_dict = sub_configs.get(bot_key, defaults.get(bot_key, {}))
            api_key_var = cfg_dict.get("api_key_env", "COINDCX_API_KEY")
            secret_key_var = cfg_dict.get("secret_key_env", "COINDCX_API_SECRET")

            api_key = (os.getenv(api_key_var) or master_api_key).strip()
            api_secret = (os.getenv(secret_key_var) or master_api_secret).strip()

            sub_cfg = SubAccountConfig(
                bot_name=bot_name,
                subaccount_id=cfg_dict.get("subaccount_id", f"ALPHA_{bot_key}_01"),
                api_key=api_key,
                api_secret=api_secret,
                allocated_wallet_inr=pool_limit if pool_limit != float("inf") else None,
                max_positions=int(cfg_dict.get("max_positions", 10)),
                default_trade_amount_inr=(
                    order_size
                    if self._config
                    else float(cfg_dict.get("default_trade_amount_inr", order_size))
                ),
                allowed_pairs=cfg_dict.get("allowed_pairs", []),
            )
            self._clients[bot_name] = CoinDCXSubAccountClient(
                config=sub_cfg,
                shared_pool_lock=self._lock,
                shared_state=self._shared_pool_state,
            )

    def update_order_size(self, new_amount: float) -> None:
        """Dynamically update configurable micro-order amount across all strategy clients."""
        clamped_amount = max(200.0, float(new_amount))
        with self._lock:
            for client in self._clients.values():
                client.config.default_trade_amount_inr = clamped_amount
            if self._config:
                self._config.order_size_inr = clamped_amount
                self._config.default_trade_amount_ste = clamped_amount
                self._config.default_trade_amount_hda = clamped_amount
                self._config.default_trade_amount_vcp = clamped_amount
                self._config.default_trade_amount_bbs = clamped_amount
        logger.info(
            "Subaccount manager dynamically updated order size to INR %.2f across all clients",
            clamped_amount,
        )

    async def fetch_live_balance(
        self, client: httpx.AsyncClient | None = None
    ) -> dict[str, Any]:
        """Fetch and synchronize real CoinDCX available INR balance."""
        master_client = self.get_client(BotName.STE)
        res = await master_client.get_balances(client=client)
        if res.get("success"):
            inr_bal = float(res.get("inr_balance", 0.0))
            with self._lock:
                self._shared_pool_state["wallet_balance_inr"] = inr_bal
            return {
                "success": True,
                "inr_balance": inr_bal,
                "inr_locked": res.get("inr_locked", 0.0),
            }
        return res

    async def get_live_balance(
        self, client: httpx.AsyncClient | None = None
    ) -> dict[str, Any]:
        """Backward-compatible alias delegating directly to fetch_live_balance."""
        return await self.fetch_live_balance(client=client)

    def get_client(self, bot_name: BotName = BotName.STE) -> CoinDCXSubAccountClient:
        """Retrieve execution client for a bot drawing from the unified pool."""
        with self._lock:
            if bot_name not in self._clients:
                return self._clients.get(BotName.STE, list(self._clients.values())[0])
            return self._clients[bot_name]

    def get_all_subaccount_telemetry(self) -> dict[str, dict[str, Any]]:
        """Return live telemetry across all strategy execution routers."""
        with self._lock:
            telemetry = {}
            for bot, client in self._clients.items():
                wb = client.wallet_balance_inr
                ab = client.available_balance_inr
                telemetry[bot.value] = {
                    "subaccount_id": client.subaccount_id,
                    "wallet_balance_inr": None if (wb is None or math.isinf(wb)) else wb,
                    "available_balance_inr": None if (ab is None or math.isinf(ab)) else ab,
                    "max_positions": client.config.max_positions,
                    "default_trade_amount_inr": client.config.default_trade_amount_inr,
                    "allowed_pairs": client.config.allowed_pairs,
                }
            return telemetry

    async def check_account_connectivity(
        self, client: httpx.AsyncClient | None = None
    ) -> dict[str, Any]:
        """Diagnostic utility to verify master API credentials against CoinDCX balances."""
        master_client = self.get_client(BotName.STE)
        return await master_client.get_balances(client=client)


# Alias for unified execution manager
CoinDCXExecutionManager = CoinDCXSubAccountManager
