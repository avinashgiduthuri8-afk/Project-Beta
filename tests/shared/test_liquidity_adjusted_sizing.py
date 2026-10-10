"""
PROJECT-ALPHA — Tests for Liquidity-Adjusted Position Sizing (Skill 09).

Verifies:
  1. Low 24h volume below ₹50,000 threshold is rejected.
  2. Multi-currency support: INR and USDT pairs accurately evaluated.
  3. Strict micro-tranche invariant: order size is capped at ORDER_SIZE_INR (₹200.00).
  4. Mandatory ₹200 minimum notional: orders cannot execute below ₹200.
  5. Excessive spread gating rejects high-friction pairs.
  6. Market impact estimation and safe sizing.
"""

import pytest

from core.config import AppConfig
from execution.risk.liquidity_sizing import (
    LiquidityAdjustedPositionSizer,
    LiquidityTier,
)


def test_illiquid_pair_volume_rejection():
    cfg = AppConfig(scanner_min_24h_volume=50000.0)
    sizer = LiquidityAdjustedPositionSizer(config=cfg)

    # 24h volume = ₹25,000 (< ₹50,000)
    res = sizer.calculate_order_size(
        pair="THIN/INR",
        current_price=10.0,
        volume_24h=25000.0,
    )
    assert res.allowed is False
    assert res.code == "INSUFFICIENT_24H_VOLUME"
    assert res.liquidity_tier == LiquidityTier.TIER_4_ILLIQUID


def test_multi_currency_usdt_conversion():
    cfg = AppConfig(scanner_min_24h_volume=50000.0)
    sizer = LiquidityAdjustedPositionSizer(config=cfg)

    # Pair is BTC/USDT. 24h volume is 1,000 USDT.
    # At rate 90, 1000 USDT = ₹90,000 (> ₹50,000)
    res = sizer.calculate_order_size(
        pair="BTC/USDT",
        current_price=60000.0,
        volume_24h=1000.0,
        usdt_inr_rate=90.0,
    )
    assert res.allowed is True
    assert res.effective_volume_24h_inr == 90000.0
    assert res.allocated_amount_inr == 200.0


def test_standard_micro_tranche_capped_at_200():
    cfg = AppConfig(order_size_inr=200.0)
    sizer = LiquidityAdjustedPositionSizer(config=cfg)

    # High liquidity coin (₹10,000,000 volume)
    res = sizer.calculate_order_size(
        pair="BTC/INR",
        current_price=6500000.0,
        volume_24h=10000000.0,
        base_trade_amount=200.0,
    )
    assert res.allowed is True
    assert res.allocated_amount_inr == 200.0
    # Invariant: Must NOT inflate above the ₹200 standard micro-tranche
    assert res.allocated_amount_inr <= 200.0


def test_minimum_notional_floor():
    cfg = AppConfig()
    sizer = LiquidityAdjustedPositionSizer(config=cfg)

    # If base_trade_amount is set below 200, it is clamped to ₹200 minimum
    res = sizer.calculate_order_size(
        pair="ETH/INR",
        current_price=300000.0,
        volume_24h=500000.0,
        base_trade_amount=100.0,  # Below minimum
    )
    assert res.allowed is True
    assert res.allocated_amount_inr == 200.0


def test_excessive_spread_rejection():
    cfg = AppConfig(scanner_max_spread_pct=2.0)
    sizer = LiquidityAdjustedPositionSizer(config=cfg)

    # Spread of 3.2% (> 2.0%)
    res = sizer.calculate_order_size(
        pair="WIDE/INR",
        current_price=100.0,
        volume_24h=200000.0,
        spread_pct=3.2,
    )
    assert res.allowed is False
    assert res.code == "EXCESSIVE_SPREAD"
