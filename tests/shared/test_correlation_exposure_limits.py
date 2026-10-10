"""
PROJECT-ALPHA — Tests for Correlation-Aware Exposure Limits (Skill 10).

Verifies:
  1. Crypto sector/cluster classification (BTC, ETH, SOL, DOGE, UNI, RENDER).
  2. Cluster position count limits (blocks 3rd position in a cluster when max=2).
  3. Cluster capital ceilings (blocks trades exceeding maximum sector capital).
  4. CapitalGuard integration respects correlation limits.
  5. Cluster exposure breakdown reporting.
"""

import pytest

from core.config import AppConfig
from core.types import BotName
from execution.risk.capital_guard import CapitalGuard
from execution.risk.correlation_limits import (
    CorrelationExposureGuard,
    CryptoCluster,
)


def test_cluster_classification():
    guard = CorrelationExposureGuard()

    assert guard.classify_coin("BTC") == CryptoCluster.BTC_BETA
    assert guard.classify_coin("BTC/INR") == CryptoCluster.BTC_BETA
    assert guard.classify_coin("ETH") == CryptoCluster.ETH_ECOSYSTEM
    assert guard.classify_coin("ARB") == CryptoCluster.ETH_ECOSYSTEM
    assert guard.classify_coin("SOL") == CryptoCluster.LAYER_1
    assert guard.classify_coin("AVAX") == CryptoCluster.LAYER_1
    assert guard.classify_coin("UNI") == CryptoCluster.DEFI
    assert guard.classify_coin("DOGE") == CryptoCluster.MEME
    assert guard.classify_coin("RENDER") == CryptoCluster.AI_DATA
    assert guard.classify_coin("UNKNOWN_COIN") == CryptoCluster.GENERAL_ALT


def test_cluster_position_limit_exceeded():
    guard = CorrelationExposureGuard(max_positions_per_cluster=2)

    # 2 Layer 1 positions already active: SOL and AVAX
    active_positions = [
        {"coin": "SOL", "pair": "SOL/INR", "entry_price": 10000.0, "qty": 0.02, "amount": 200.0},
        {"coin": "AVAX", "pair": "AVAX/INR", "entry_price": 2000.0, "qty": 0.1, "amount": 200.0},
    ]

    # Attempt to open 3rd Layer 1 coin (ADA)
    dec = guard.check_exposure(
        bot=BotName.STE,
        candidate_coin="ADA",
        requested_amount=200.0,
        active_positions=active_positions,
    )
    assert dec.allowed is False
    assert dec.code == "BLOCKED_CORRELATION_LIMIT"
    assert "LAYER_1" in dec.reason
    assert "limit reached" in dec.reason

    # Attempt to open DeFi coin (UNI) is allowed (0 in DeFi)
    dec_defi = guard.check_exposure(
        bot=BotName.STE,
        candidate_coin="UNI",
        requested_amount=200.0,
        active_positions=active_positions,
    )
    assert dec_defi.allowed is True


def test_cluster_capital_ceiling():
    guard = CorrelationExposureGuard(max_cluster_capital_inr=500.0)

    # Memecoin DOGE has ₹400 already deployed
    active_positions = [
        {"coin": "DOGE", "pair": "DOGE/INR", "entry_price": 15.0, "qty": 26.6, "amount": 400.0},
    ]

    # Adding ₹200 on SHIB would bring MEME cluster capital to ₹600 (> ₹500)
    dec = guard.check_exposure(
        bot=BotName.BBS,
        candidate_coin="SHIB",
        requested_amount=200.0,
        active_positions=active_positions,
    )
    assert dec.allowed is False
    assert dec.code == "BLOCKED_CORRELATION_LIMIT"
    assert "capital ceiling exceeded" in dec.reason


def test_capital_guard_correlation_integration():
    cfg = AppConfig(enforce_single_coin_lock=False)
    corr_guard = CorrelationExposureGuard(max_positions_per_cluster=2)
    capital_guard = CapitalGuard(config=cfg, correlation_guard=corr_guard)

    active_positions = [
        {"coin": "NEAR", "pair": "NEAR/INR", "entry_price": 400.0, "qty": 0.5, "amount": 200.0},
        {"coin": "SUI", "pair": "SUI/INR", "entry_price": 150.0, "qty": 1.33, "amount": 200.0},
    ]

    # Candidate coin is APT (another Layer 1)
    dec = capital_guard.check_trade(
        bot=BotName.STE,
        requested_amount=200.0,
        current_bot_deployed=400.0,
        total_deployed=400.0,
        current_bot_positions=2,
        active_positions=active_positions,
        current_coin="APT",
    )
    assert dec.allowed is False
    assert dec.code == "BLOCKED_CORRELATION_LIMIT"


def test_cluster_breakdown():
    guard = CorrelationExposureGuard()
    active_positions = [
        {"coin": "BTC", "pair": "BTC/INR", "entry_price": 6000000.0, "qty": 0.0001, "amount": 600.0},
        {"coin": "SOL", "pair": "SOL/INR", "entry_price": 10000.0, "qty": 0.02, "amount": 200.0},
        {"coin": "ADA", "pair": "ADA/INR", "entry_price": 50.0, "qty": 4.0, "amount": 200.0},
    ]
    breakdown = guard.get_cluster_breakdown(active_positions)
    assert breakdown[CryptoCluster.BTC_BETA.value]["count"] == 1
    assert breakdown[CryptoCluster.BTC_BETA.value]["capital_inr"] == 600.0
    assert breakdown[CryptoCluster.LAYER_1.value]["count"] == 2
    assert breakdown[CryptoCluster.LAYER_1.value]["capital_inr"] == 400.0
