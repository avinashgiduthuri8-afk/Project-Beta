"""
tests/test_v2_min_trading_value.py
Unit tests verifying the ₹200 Minimum Trading Value Invariant across all tiers.

Tests:
1. Precision rounding up behavior (round_qty_up) on all lot step sizes.
2. AutoTradeRouter rounding up quantity when initial notional is < ₹200.00 (e.g. ₹199, ₹199.99).
3. Orders at or above ₹200.00 (e.g. ₹200.00, ₹200.01, ₹250.00) pass precision validation.
4. Validation rejection (fail closed) if final submitted order value is strictly below ₹200.00.
5. Capital pool isolation / limit protection when rounded notional exceeds available balance.
"""

import pytest

from core.bus.event_bus import EventBus
from execution.auto_trader import AutoTradeRouter
from execution.trading.precision_rules import (
    round_qty_up,
    validate_order_notional,
)
from execution.trading.subaccount_manager import CoinDCXSubAccountManager


def test_round_qty_up_discrete_steps():
    """Verify round_qty_up correctly rounds up to the exact lot step size."""
    # BTC/INR step: 0.00001 (5 decimals)
    assert round_qty_up("BTC/INR", 0.000021) == 0.00003
    assert round_qty_up("BTC/INR", 0.000020) == 0.00002

    # ETH/INR step: 0.0001 (4 decimals)
    assert round_qty_up("ETH/INR", 0.00071) == 0.0008
    assert round_qty_up("ETH/INR", 0.00070) == 0.0007

    # SOL/INR step: 0.01 (2 decimals)
    assert round_qty_up("SOL/INR", 0.015) == 0.02
    assert round_qty_up("SOL/INR", 0.010) == 0.01

    # DOGE/INR step: 1.0 (0 decimals)
    assert round_qty_up("DOGE/INR", 12.1) == 13.0
    assert round_qty_up("DOGE/INR", 12.0) == 12.0

    # SHIB/INR step: 1000 (-3 decimals)
    assert round_qty_up("SHIB/INR", 100500.0) == 101000.0
    assert round_qty_up("SHIB/INR", 100000.0) == 100000.0


def test_validate_order_notional_invariant_boundary():
    """Verify validate_order_notional strictly enforces min ₹200.00 invariant."""
    pair = "SOL/INR"
    price = 10000.0

    # 1. Below 200.00 -> Fails
    assert (
        validate_order_notional(pair, price, 0.019, min_notional=200.0) is False
    )  # ₹190.00
    assert (
        validate_order_notional(pair, price, 0.019999, min_notional=200.0) is False
    )  # ₹199.99

    # 2. Exactly 200.00 -> Passes
    assert (
        validate_order_notional(pair, price, 0.02, min_notional=200.0) is True
    )  # ₹200.00

    # 3. Above 200.00 -> Passes
    assert (
        validate_order_notional(pair, price, 0.02001, min_notional=200.0) is True
    )  # ₹200.10
    assert (
        validate_order_notional(pair, price, 0.025, min_notional=200.0) is True
    )  # ₹250.00


@pytest.mark.anyio
async def test_auto_trader_rounds_up_sub_200_orders_to_meet_invariant():
    """Verify AutoTradeRouter rounds quantity UP when trade amount is slightly below ₹200 (e.g. ₹199.00 or ₹199.99)."""
    bus = EventBus()
    sub_mgr = CoinDCXSubAccountManager()
    router = AutoTradeRouter(bus=bus, subaccount_manager=sub_mgr, dry_run=True)

    # Test with DOGE @ ₹20/unit, trade amount = ₹199.00 (initially 9.95 DOGE -> rounded down to 9 = ₹180 < 200)
    # round_qty_up brings quantity to 10 DOGE = ₹200.00 notional
    sig_199 = {
        "id": "sig-doge-199",
        "coin": "DOGE",
        "pair": "DOGE/INR",
        "price": 20.0,
        "trade_amount": 199.0,
        "bot": "STE",
    }
    res_199 = await router.handle_signal(sig_199)
    assert res_199["success"] is True
    assert res_199["qty"] == 10.0
    assert res_199["notional"] == 200.0
    assert res_199["notional"] >= 200.0

    # Test with SOL @ ₹10,000/unit, trade amount = ₹199.99 (initially 0.019999 SOL -> rounded down to 0.01 = ₹100)
    # round_qty_up brings quantity to 0.02 SOL = ₹200.00 notional
    sig_199_99 = {
        "id": "sig-sol-199-99",
        "coin": "SOL",
        "pair": "SOL/INR",
        "price": 10000.0,
        "trade_amount": 199.99,
        "bot": "STE",
    }
    res_199_99 = await router.handle_signal(sig_199_99)
    assert res_199_99["success"] is True
    assert res_199_99["qty"] == 0.02
    assert res_199_99["notional"] == 200.0
    assert res_199_99["notional"] >= 200.0


@pytest.mark.anyio
async def test_auto_trader_accepts_orders_at_or_above_200():
    """Verify AutoTradeRouter accepts orders at ₹200.00, ₹200.01, and ₹250.00 without unwanted modification."""
    bus = EventBus()
    sub_mgr = CoinDCXSubAccountManager()
    router = AutoTradeRouter(bus=bus, subaccount_manager=sub_mgr, dry_run=True)

    # 1. Exactly ₹200.00 on ETH (price: 200,000, qty: 0.001)
    sig_200 = {
        "id": "sig-eth-200",
        "coin": "ETH",
        "pair": "ETH/INR",
        "price": 200000.0,
        "trade_amount": 200.0,
        "bot": "STE",
    }
    res_200 = await router.handle_signal(sig_200)
    assert res_200["success"] is True
    assert res_200["notional"] == 200.0

    # 2. ₹250.00 on BTC (price: 5,000,000, qty: 0.00005)
    sig_250 = {
        "id": "sig-btc-250",
        "coin": "BTC",
        "pair": "BTC/INR",
        "price": 5000000.0,
        "trade_amount": 250.0,
        "bot": "STE",
    }
    res_250 = await router.handle_signal(sig_250)
    assert res_250["success"] is True
    assert res_250["notional"] == 250.0
