"""Comprehensive End-to-End Test Suite for PROJECT-BETA Platform."""

from datetime import datetime, timedelta
import pytest

from core.models import Candle, ScannerCandidate, TradePlan, AccountBalance, Position
from core.enums import SetupType, OrderSide, ProductType, Exchange, AISignalDecision
from scanner.indicators import Indicators
from scanner.setups import SetupDetector
from scoring.scorecard import SignalScorecard
from scanner.mtf_scanner import MultiTimeframeScanner
from ai_intel.advisor import AIThesisAdvisor
from trade.constructor import TradeConstructor
from risk.risk_engine import RiskEngine
from risk.market_clock import MarketClock
from brokers.paper_broker import PaperBroker
from oms.execution_router import ExecutionRouter
from oms.order_manager import OrderManager
from learning.evaluator import StrategyEvaluator
from learning.backtest_engine import BacktestEngine
from hulk_bridge.event_bus import HulkEventBus, HulkEventTypes


def create_sample_candles(count: int = 250, base_price: float = 2000.0) -> list[Candle]:
    now = datetime.now()
    candles = []
    for i in range(count, -1, -1):
        dt = now - timedelta(days=i)
        p = base_price + (count - i) * 3.0  # Uptrend
        candles.append(
            Candle(
                symbol="RELIANCE",
                timeframe="1d",
                timestamp=dt,
                open=p - 10.0,
                high=p + 20.0,
                low=p - 15.0,
                close=p,
                volume=1500000,
                delivery_volume=900000,
                vwap=p + 1.0,
                is_closed=True,
            )
        )
    return candles


def test_indicators_math_and_lookbacks():
    candles = create_sample_candles(250)
    
    # 1. EMA 200 calculation
    ema_200 = Indicators.calculate_ema(candles, 200)
    assert ema_200 > 0
    assert ema_200 < candles[-1].close  # Price is in uptrend above 200 EMA

    # 2. ATR 14 calculation
    atr = Indicators.calculate_atr(candles, 14)
    assert atr > 0.0

    # 3. RSI 14 calculation
    rsi = Indicators.calculate_rsi(candles, 14)
    assert 0.0 <= rsi <= 100.0

    # 4. Mansfield RS vs Benchmark
    nifty = create_sample_candles(250, base_price=24000.0)
    mansfield_rs = Indicators.calculate_mansfield_rs(candles, nifty, 50)
    assert isinstance(mansfield_rs, float)


def test_setup_detectors():
    candles = create_sample_candles(250)
    
    # Test Pocket Pivot detection
    is_pp, pp_meta = SetupDetector.detect_pocket_pivot(candles)
    assert isinstance(is_pp, bool)
    assert isinstance(pp_meta, dict)

    # Test NR7 Squeeze detection
    is_nr7, nr7_meta = SetupDetector.detect_nr7_squeeze(candles)
    assert isinstance(is_nr7, bool)

    # Test High Delivery Breakout
    is_del, del_meta = SetupDetector.detect_high_delivery_breakout(candles, delivery_pct=65.0, avg_delivery_pct=40.0)
    assert is_del is True
    assert del_meta["delivery_percentage"] == 65.0


def test_scorecard_and_hard_gates():
    candles = create_sample_candles(250, base_price=2000.0)

    # Valid candidate test
    score = SignalScorecard.evaluate(
        daily_candles=candles,
        h1_candles=candles[-20:],
        m15_candles=candles[-15:],
        setup_type=SetupType.MINERVINI_VCP,
        setup_metadata={"volume_dry_up": True},
        mansfield_rs=8.5,
        delivery_pct=60.0,
        avg_delivery_pct=42.0,
    )
    assert score.hard_gates_passed is True
    assert score.total_score >= 70.0
    assert score.trend_regime_score > 0
    assert score.volume_delivery_score > 0

    # Hard Gate Breach Test: Price below 200 EMA
    downtrend_candles = list(reversed(candles))  # Price is now crashing below 200 EMA
    failed_score = SignalScorecard.evaluate(
        daily_candles=downtrend_candles,
        h1_candles=[],
        m15_candles=[],
        setup_type=SetupType.MINERVINI_VCP,
        setup_metadata={},
        mansfield_rs=-10.0,
        delivery_pct=30.0,
        avg_delivery_pct=40.0,
    )
    assert failed_score.hard_gates_passed is False
    assert failed_score.total_score == 0.0
    assert "Hard Gate Breach" in failed_score.rejection_reason


def test_ai_thesis_advisor():
    candles = create_sample_candles(250)
    advisor = AIThesisAdvisor()

    score = SignalScorecard.evaluate(
        daily_candles=candles,
        h1_candles=[],
        m15_candles=[],
        setup_type=SetupType.MINERVINI_VCP,
        setup_metadata={"volume_dry_up": True},
        mansfield_rs=6.0,
        delivery_pct=58.0,
        avg_delivery_pct=40.0,
    )

    candidate = ScannerCandidate(
        symbol="RELIANCE",
        sector="ENERGY",
        ltp=candles[-1].close,
        setup_type=SetupType.MINERVINI_VCP,
        score_breakdown=score,
        relative_strength_vs_nifty=6.0,
        daily_volume=1500000,
        delivery_pct=58.0,
        atr_14=25.0,
        ema_20=2700.0,
        ema_50=2650.0,
        ema_200=2450.0,
    )

    ai_eval = advisor.evaluate_candidate(candidate)
    assert ai_eval.decision in (AISignalDecision.CONFIRMED, AISignalDecision.CHALLENGED)
    assert ai_eval.confidence_score >= 0.70
    assert ai_eval.invalidation_price > 0.0


def test_trade_construction_and_rms():
    candles = create_sample_candles(250)
    advisor = AIThesisAdvisor()
    score = SignalScorecard.evaluate(candles, [], [], SetupType.MINERVINI_VCP, {}, 5.0, 55.0, 40.0)
    candidate = ScannerCandidate(
        symbol="TCS",
        sector="IT",
        ltp=4000.0,
        setup_type=SetupType.MINERVINI_VCP,
        score_breakdown=score,
        relative_strength_vs_nifty=5.0,
        daily_volume=1000000,
        delivery_pct=55.0,
        atr_14=40.0,
        ema_20=3900.0,
        ema_50=3800.0,
        ema_200=3500.0,
    )

    ai_eval = advisor.evaluate_candidate(candidate)
    balance = AccountBalance(total_capital=100000.0, available_margin=100000.0)

    # 1. Trade Construction
    plan = TradeConstructor.construct_plan(candidate, ai_eval, balance, risk_per_trade_pct=1.0, min_rr_ratio=2.0)
    assert plan is not None
    assert plan.symbol == "TCS"
    assert plan.risk_reward_ratio >= 2.0
    assert plan.stop_loss < plan.entry_price
    assert plan.target_price > plan.entry_price

    # 2. Risk Engine Validation
    clock = MarketClock()
    risk_engine = RiskEngine(max_daily_loss=3000.0, market_clock=clock)
    req = type('Req', (), {
        'symbol': 'TCS',
        'price': plan.entry_price,
        'quantity': plan.calculated_quantity,
    })()

    # Breached Daily Loss Test
    balance_loss = AccountBalance(total_capital=96000.0, available_margin=50000.0, realized_pnl=-3500.0, unrealized_pnl=0.0)
    is_valid, reason = risk_engine.validate_order(req, balance_loss, [], candidate_sector="IT")
    assert is_valid is False
    assert "Daily max loss limit" in reason


def test_hulk_event_bus():
    hulk_bus = HulkEventBus()
    received_events = []

    hulk_bus.subscribe(HulkEventTypes.SCANNER_CANDIDATE_FOUND, lambda evt: received_events.append(evt))
    hulk_bus.publish(HulkEventTypes.SCANNER_CANDIDATE_FOUND, {"symbol": "INFY", "score": 88.5})

    assert len(received_events) == 1
    assert received_events[0]["data"]["symbol"] == "INFY"
    assert received_events[0]["event_type"] == HulkEventTypes.SCANNER_CANDIDATE_FOUND


def test_backtest_engine():
    candles = create_sample_candles(150, base_price=1500.0)
    bt = BacktestEngine(initial_capital=100000.0)
    results = bt.run_backtest("INFY", candles, min_score=70.0)

    assert "final_capital" in results
    assert "total_trades" in results
    assert results["initial_capital"] == 100000.0
