"""
Safety gate validation for LIVE execution mode.
Enforces 7 strict checks before allowing LIVE_MICROCASH activation:
1. Credentials presence (API key and Secret)
2. Risk Engine health (circuit breaker not open)
3. Emergency stop inactive
4. CoinDCX connectivity & subaccount manager resolution
5. CoinDCX live INR balance retrieval success
6. CoinDCX INR balance validity (non-negative)
7. CoinDCX available INR balance >= configured order amount
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("background.production.safety_gate")


async def verify_live_mode_safety_gate(
    config: Any,
    subaccount_manager: Any | None = None,
    risk_service: Any | None = None,
) -> tuple[bool, str, float | None]:
    """
    Verify all safety conditions before enabling LIVE_MICROCASH mode.

    Returns:
        (passed: bool, reason: str, live_inr_balance: float | None)
    """
    # 1. Credentials Check
    key = getattr(config, "coindcx_api_key", None) or getattr(config, "coindcx_key", None) or getattr(config, "coindcx_live_api_key", None)
    secret = getattr(config, "coindcx_api_secret", None) or getattr(config, "coindcx_secret", None) or getattr(config, "coindcx_live_api_secret", None)
    if not key or not secret:
        return False, "Missing CoinDCX API credentials (API key or secret).", None

    # 2. Risk Engine / Circuit Breaker Check
    if risk_service:
        cb = getattr(risk_service, "circuit_breaker", None)
        if cb:
            is_open = getattr(cb, "is_open", False)
            if callable(is_open):
                is_open = is_open()
            if is_open is True:
                return False, "Risk Engine: Circuit breaker is open / tripped.", None

            em_stop = getattr(cb, "emergency_stop", False)
            if callable(em_stop):
                em_stop = em_stop()
            if em_stop is True:
                return False, "Risk Engine: Emergency stop is active.", None

    # 3. Subaccount Manager / Client Connectivity & Balance Retrieval
    if not subaccount_manager:
        try:
            from execution.trading.subaccount_manager import CoinDCXSubAccountManager
            subaccount_manager = CoinDCXSubAccountManager(config)
        except Exception as exc:
            return False, f"CoinDCX Subaccount Manager unavailable: {exc}", None

    try:
        if hasattr(subaccount_manager, "get_live_balance"):
            bal_res = await subaccount_manager.get_live_balance()
        elif hasattr(subaccount_manager, "fetch_live_balance"):
            bal_res = await subaccount_manager.fetch_live_balance()
        elif hasattr(subaccount_manager, "get_balances"):
            bal_res = await subaccount_manager.get_balances()
        else:
            return False, "CoinDCX Subaccount Manager does not support balance fetching.", None
    except Exception as exc:
        return False, f"CoinDCX connection error during balance check: {exc}", None

    if not isinstance(bal_res, dict) or not bal_res.get("success", False):
        err = bal_res.get("error", "Unknown API error") if isinstance(bal_res, dict) else "Invalid balance response"
        return False, f"CoinDCX API balance retrieval failed: {err}", None

    # 4. INR Balance Validity
    inr_bal = bal_res.get("inr_balance")
    if inr_bal is None or not isinstance(inr_bal, (int, float)):
        return False, "CoinDCX INR balance is invalid or missing from response.", None

    inr_bal = float(inr_bal)
    if inr_bal < 0:
        return False, f"CoinDCX INR balance is negative: ₹{inr_bal:.2f}", inr_bal

    # 5. Order Amount Threshold Check
    order_size = max(200.0, float(getattr(config, "order_size_inr", 200.0)))
    if inr_bal < order_size:
        return (
            False,
            f"Insufficient CoinDCX INR balance: ₹{inr_bal:,.2f} is below configured order size ₹{order_size:,.2f}.",
            inr_bal,
        )

    return True, "READY", inr_bal
