"""Unit & Integration tests for the merged Stockscanner suite in Project-Beta."""

import pytest
from scanner.universe import get_universe_symbols
from scanner.regime import MarketRegimeDetector
from scanner.sectors import SectorStrengthAnalyzer
from risk.extension_safety import ExtensionAndSafetyFilter
from scanner.lifecycle import SignalLifecycleManager, SignalState
from core.models import ScannerCandidate, ScoreBreakdown
from core.enums import SetupType, MarketRegime


def test_universe_symbols():
    nifty50 = get_universe_symbols("NIFTY_50")
    assert len(nifty50) == 20
    assert any(s["symbol"] == "RELIANCE" for s in nifty50)

    nifty100 = get_universe_symbols("NIFTY_100")
    assert len(nifty100) == 30
    assert any(s["symbol"] == "HCLTECH" for s in nifty100)


def test_market_regime_detector():
    # 1. Bullish Regime
    res_bull = MarketRegimeDetector.evaluate_regime(nifty_change_pct=0.8, bank_change_pct=0.6, vix_value=13.5)
    assert res_bull["regime"] == MarketRegime.BULLISH_TRENDING
    assert res_bull["long_setups_allowed"] is True

    # 2. High Volatility / VIX Spike
    res_vix = MarketRegimeDetector.evaluate_regime(nifty_change_pct=-1.5, bank_change_pct=-1.8, vix_value=23.5, vix_change_pct=15.0)
    assert res_vix["regime"] == MarketRegime.HIGH_VOLATILITY_EXPANSION
    assert res_vix["long_setups_allowed"] is False


def test_sector_strength_analyzer():
    analyzer = SectorStrengthAnalyzer()
    leading = analyzer.get_leading_sectors()
    assert "IT" in leading
    assert "ENERGY" in leading
    assert analyzer.is_sector_in_momentum("IT") is True
    assert analyzer.is_sector_in_momentum("PHARMA") is False


def test_extension_and_safety_filter():
    safety = ExtensionAndSafetyFilter(max_ema20_atr_dist=2.2, max_chase_pct=4.0)

    # 1. Optimal entry test
    is_safe, msg, _ = safety.evaluate_safety(
        symbol="RELIANCE",
        price=2850.0,
        ema_20=2820.0,
        atr_14=30.0,
        pivot_price=2840.0,
    )
    assert is_safe is True

    # 2. Overextended chasing test (> 2.2x ATR above 20 EMA)
    is_safe_ext, msg_ext, _ = safety.evaluate_safety(
        symbol="INFY",
        price=2000.0,
        ema_20=1850.0,  # 150 pts above / 25 ATR = 6x ATR!
        atr_14=25.0,
    )
    assert is_safe_ext is False
    assert "Overextended" in msg_ext

    # 3. SEBI Surveillance list test
    is_safe_sebi, msg_sebi, _ = safety.evaluate_safety(
        symbol="SUZLON",
        price=50.0,
        ema_20=48.0,
        atr_14=2.0,
    )
    assert is_safe_sebi is False
    assert "SEBI ASM/GSM Surveillance List" in msg_sebi


def test_signal_lifecycle():
    mgr = SignalLifecycleManager(max_signal_age_minutes=120)
    score = ScoreBreakdown(total_score=85.0)
    cand = ScannerCandidate(
        symbol="TCS",
        sector="IT",
        ltp=4000.0,
        setup_type=SetupType.MINERVINI_VCP,
        score_breakdown=score,
    )
    mgr.register_signal(cand)
    assert "TCS" in mgr.active_signals
    assert mgr.active_signals["TCS"]["state"] == SignalState.NEW
