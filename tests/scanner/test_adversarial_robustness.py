"""
PROJECT-ALPHA — Adversarial Signal Robustness Unit Tests (Skill 03).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pytest

from core.types import MarketState, Priority, RiskLevel, Signal
from scanner.adversarial import (
    AdversarialStressConfig,
    AdversarialStressEngine,
    AdversarialStressResult,
)


def _make_dummy_signal(
    coin: str = "BTC",
    score: int = 92,
    price: float = 100.0,
    tp: float | None = None,
    sl: float | None = None,
) -> Signal:
    now = datetime.now(timezone.utc)
    raw = {"price": price}
    if tp is not None:
        raw["take_profit"] = tp
    if sl is not None:
        raw["stop_loss"] = sl

    return Signal(
        id=f"sig_{coin}_123",
        coin=coin,
        pair=f"{coin}/INR",
        market_state=MarketState.BREAKOUT,
        opportunity_type="momentum_trade",
        priority=Priority.ELITE if score >= 90 else Priority.HIGH,
        risk_level=RiskLevel.LOW,
        score=score,
        confidence=90,
        coin_class="A",
        mtf_alignment=True,
        generated_at=now,
        expires_at=now + timedelta(seconds=300),
        raw_payload=raw,
    )


def test_inject_ohlc_noise_preserves_invariants():
    engine = AdversarialStressEngine()
    candles = [
        {
            "timestamp": 1000 + i * 60,
            "open": 100.0 + i,
            "high": 105.0 + i,
            "low": 95.0 + i,
            "close": 102.0 + i,
            "volume": 500.0,
        }
        for i in range(25)
    ]

    # Test with aggressive 2.0% noise
    noisy = engine.inject_ohlc_noise(candles, noise_std_pct=2.0, seed=12345)
    assert len(noisy) == len(candles)

    for orig, c in zip(candles, noisy):
        # 1. Price positivity
        assert c["open"] > 0
        assert c["close"] > 0
        assert c["high"] > 0
        assert c["low"] > 0
        assert c["volume"] >= 0

        # 2. Strict OHLC geometric envelope invariants
        assert c["high"] >= max(c["open"], c["close"])
        assert c["low"] <= min(c["open"], c["close"])
        assert c["high"] >= c["low"]

        # 3. Immutability of input dataset
        assert orig["open"] != c["open"] or orig["high"] != c["high"]


def test_stress_test_execution_rr_nominal_and_adverse():
    engine = AdversarialStressEngine()

    # Long setup: Entry=100, SL=96 (4% risk), TP=108 (8% reward) -> Nominal R:R = 2.0
    passed, stressed_rr, max_slip_bps, nominal_rr = engine.stress_test_execution_rr(
        entry_price=100.0,
        sl_price=96.0,
        tp_price=108.0,
        slippage_bps=25.0,  # 0.25% slippage
        min_acceptable_rr=1.0,
        is_long=True,
    )

    assert nominal_rr == 2.0
    assert passed is True
    # Stressed R:R should be lower than nominal but still well above 1.0
    assert 1.0 < stressed_rr < nominal_rr
    # Max tolerated slippage should be around ~99 bps
    assert 90.0 < max_slip_bps < 110.0


def test_stress_test_execution_rr_tight_failure():
    engine = AdversarialStressEngine()

    # Long setup with razor-thin R:R: Entry=100, SL=98 (2% risk), TP=101.5 (1.5% reward)
    # Nominal R:R = 0.75 < 1.0
    passed, stressed_rr, max_slip_bps, nominal_rr = engine.stress_test_execution_rr(
        entry_price=100.0,
        sl_price=98.0,
        tp_price=101.5,
        slippage_bps=20.0,
        min_acceptable_rr=1.0,
        is_long=True,
    )

    assert nominal_rr == 0.75
    assert passed is False
    assert max_slip_bps == 0.0  # Cannot tolerate any slippage


def test_latency_drift_chasing_and_invalidation():
    engine = AdversarialStressEngine()

    # Entry=100, SL=95 (dist=5), TP=115 (dist=15)
    # 1. Healthy execution near entry: current_price=101 (progress = 1/15 = 6.6%)
    chase, inval, reasons = engine.evaluate_latency_drift(
        entry_price=100.0,
        current_price=101.0,
        sl_price=95.0,
        tp_price=115.0,
        max_chase_pct=0.40,
        max_inval_pct=0.40,
    )
    assert chase is False
    assert inval is False
    assert len(reasons) == 0

    # 2. Chase risk: current_price=108 (progress = 8/15 = 53.3% > 40%)
    chase, inval, reasons = engine.evaluate_latency_drift(
        entry_price=100.0,
        current_price=108.0,
        sl_price=95.0,
        tp_price=115.0,
        max_chase_pct=0.40,
        max_inval_pct=0.40,
    )
    assert chase is True
    assert inval is False
    assert any("Chase risk" in r for r in reasons)

    # 3. Invalidation risk: current_price=97 (progress = 3/5 = 60% > 40%)
    chase, inval, reasons = engine.evaluate_latency_drift(
        entry_price=100.0,
        current_price=97.0,
        sl_price=95.0,
        tp_price=115.0,
        max_chase_pct=0.40,
        max_inval_pct=0.40,
    )
    assert chase is False
    assert inval is True
    assert any("Invalidation risk" in r for r in reasons)


def test_monte_carlo_noise_stress_evaluation():
    engine = AdversarialStressEngine()

    candles = [
        {"close": 100.0 + i, "open": 99.0 + i, "high": 102.0 + i, "low": 98.0 + i, "volume": 100.0}
        for i in range(30)
    ]

    # Robust evaluator: Strong uptrend where last close > first close + 10
    def robust_evaluator(c_list):
        return c_list[-1]["close"] > c_list[0]["close"] + 10.0

    stability, scores, mean_s, std_s = engine.run_monte_carlo_noise_stress(
        candles=candles,
        evaluator_fn=robust_evaluator,
        n_simulations=30,
        noise_std_pct=0.5,
    )
    assert stability >= 0.90
    assert mean_s == 100.0

    # Fragile evaluator: Requires close to be exactly between 128.9 and 129.1
    def fragile_evaluator(c_list):
        return 128.9 <= c_list[-1]["close"] <= 129.1

    fragile_stability, _, _, _ = engine.run_monte_carlo_noise_stress(
        candles=candles,
        evaluator_fn=fragile_evaluator,
        n_simulations=30,
        noise_std_pct=1.0,
    )
    assert fragile_stability < 0.50


def test_evaluate_signal_robustness_elite_and_chase():
    engine = AdversarialStressEngine()

    # 1. Elite Signal (score 92): default TP is 12.5% (10-15%), SL is 4% (PROJECT-ALPHA rules)
    sig = _make_dummy_signal(score=92, price=1000.0)
    res = engine.evaluate_signal_robustness(sig, current_price=1005.0)

    assert isinstance(res, AdversarialStressResult)
    assert res.is_robust is True
    assert res.nominal_rr >= 3.0  # 12.5% / 4% = 3.125
    assert res.stressed_rr > 2.5
    assert res.chase_risk is False
    assert res.invalidation_risk is False

    # Super Elite Signal (score 96): default TP is 22% (20-25%), SL is 4% -> R:R = 5.5
    sig_super = _make_dummy_signal(score=96, price=1000.0)
    res_super = engine.evaluate_signal_robustness(sig_super, current_price=1005.0)
    assert res_super.nominal_rr >= 4.5
    assert res_super.stressed_rr > 4.0

    # 2. Signal with Chase Condition: price has already surged to 1150 (75% of TP)
    sig_chased = _make_dummy_signal(score=92, price=1000.0)
    res_chased = engine.evaluate_signal_robustness(sig_chased, current_price=1150.0)

    assert res_chased.is_robust is False
    assert res_chased.chase_risk is True
    assert any("Chase risk" in r for r in res_chased.rejection_reasons)
