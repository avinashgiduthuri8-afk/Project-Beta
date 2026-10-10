"""
Tests for C2 High-Conviction Crypto Scanner Architecture:
  - 5-Layer Evaluation Engine (Chart, Indicators, Sentiment, News, Confluence)
  - Strict Rejection Gate (Default to REJECT unless all layers pass)
  - Maximum 1–2 Signals Output Limit (Quality > Quantity)
  - 0 Signal Output on weak or conflicting market conditions
  - Integration with ScannerService
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.types import MarketState, Priority, RiskLevel, Signal
from scanner.confluence_engine import (
    ChartStructureEvaluator,
    ConfluenceEngine,
    IndicatorEvaluator,
    MarketSentimentEvaluator,
    NewsEventsEvaluator,
)


def _make_test_signal(
    coin: str = "BTC",
    score: int = 90,
    priority: Priority = Priority.ELITE,
    market_state: MarketState = MarketState.BREAKOUT,
    mtf_alignment: bool = True,
    risk_level: RiskLevel = RiskLevel.LOW,
) -> Signal:
    now = datetime.now(timezone.utc)
    return Signal(
        id=f"sig_{coin}_{now.timestamp()}",
        coin=coin,
        pair=f"{coin}_USDT",
        market_state=market_state,
        opportunity_type=(
            OppType.MOMENTUM_TRADE
            if hasattr(OppType, "MOMENTUM_TRADE")
            else "momentum_trade"
        ),
        priority=priority,
        risk_level=risk_level,
        score=score,
        confidence=90,
        coin_class="A",
        mtf_alignment=mtf_alignment,
        generated_at=now,
        expires_at=now + timedelta(seconds=300),
    )


from core.types import OppType


class TestChartStructureEvaluator:
    def test_breakout_bull_trend_high_score(self):
        evaluator = ChartStructureEvaluator()
        sig = _make_test_signal(market_state=MarketState.BREAKOUT)
        res = evaluator.evaluate({}, sig)
        assert res.passed is True
        assert res.score >= 80

    def test_downtrend_causes_rejection(self):
        evaluator = ChartStructureEvaluator()
        sig = _make_test_signal(market_state=MarketState.DOWNTREND)
        res = evaluator.evaluate({}, sig)
        assert res.passed is False
        assert len(res.reasons) > 0


class TestIndicatorEvaluator:
    def test_mtf_alignment_required(self):
        evaluator = IndicatorEvaluator()
        sig_aligned = _make_test_signal(mtf_alignment=True, score=90)
        res_aligned = evaluator.evaluate({}, sig_aligned)
        assert res_aligned.passed is True

        sig_unaligned = _make_test_signal(mtf_alignment=False, score=90)
        res_unaligned = evaluator.evaluate({}, sig_unaligned)
        assert res_unaligned.passed is False
        assert "multi-timeframe" in res_unaligned.reasons[0].lower()

    def test_volume_ratio_rejection(self):
        evaluator = IndicatorEvaluator()
        sig = _make_test_signal(mtf_alignment=True, score=90)
        # Volume ratio < 0.8 should reduce score by 20 and fail
        res = evaluator.evaluate({"volume_ratio": 0.5}, sig)
        assert res.passed is False
        assert "Insufficient relative volume" in res.reasons[0]
        
        # Volume ratio >= 0.8 should pass
        res_pass = evaluator.evaluate({"volume_ratio": 1.2}, sig)
        assert res_pass.passed is True
        
    def test_rsi_momentum_rejection(self):
        evaluator = IndicatorEvaluator()
        sig = _make_test_signal(mtf_alignment=True, score=90)
        # RSI < 60 should reduce score by 15 and fail
        res = evaluator.evaluate({"rsi": 55.0}, sig)
        assert res.passed is False
        assert "Weak momentum RSI" in res.reasons[0]
        
        # RSI >= 60 should pass
        res_pass = evaluator.evaluate({"rsi": 65.0}, sig)
        assert res_pass.passed is True


class TestMarketSentimentEvaluator:
    def test_risk_on_bullish_btc_passes(self):
        evaluator = MarketSentimentEvaluator()
        evaluator.update_market_state(
            btc_trend="BULLISH", eth_trend="BULLISH", market_regime="RISK_ON"
        )
        sig = _make_test_signal()
        res = evaluator.evaluate({}, sig)
        assert res.passed is True

    def test_risk_off_causes_rejection(self):
        evaluator = MarketSentimentEvaluator()
        evaluator.update_market_state(
            btc_trend="BEARISH", eth_trend="BEARISH", market_regime="RISK_OFF"
        )
        sig = _make_test_signal()
        res = evaluator.evaluate({}, sig)
        assert res.passed is False
        assert any("risk_off" in r.lower() for r in res.reasons)


class TestNewsEventsEvaluator:
    def test_clean_news_passes(self):
        evaluator = NewsEventsEvaluator()
        sig = _make_test_signal()
        res = evaluator.evaluate({"news": {"has_negative_news": False}}, sig)
        assert res.passed is True

    def test_negative_news_causes_rejection(self):
        evaluator = NewsEventsEvaluator()
        sig = _make_test_signal()
        res = evaluator.evaluate({"news": {"has_negative_news": True}}, sig)
        assert res.passed is False
        assert "negative news" in res.reasons[0].lower()


class TestConfluenceEngine:
    def test_strict_rejection_mentality(self):
        engine = ConfluenceEngine(strict_threshold=85, max_signals=2)
        engine.update_market_sentiment("BULLISH", "BULLISH", "RISK_ON")

        # 1 strong candidate + 1 weak candidate
        strong_sig = _make_test_signal(
            "SOL", score=95, priority=Priority.ELITE, mtf_alignment=True
        )
        weak_sig = _make_test_signal(
            "DOGE", score=60, priority=Priority.WATCH, mtf_alignment=False
        )

        raw_candidates = [{"coin": "SOL"}, {"coin": "DOGE"}]
        accepted, all_results = engine.evaluate_candidates(
            raw_candidates, [strong_sig, weak_sig]
        )

        # Weak candidate must be rejected by strict rejection gate
        accepted_coins = [s.coin for s in accepted]
        assert "SOL" in accepted_coins
        assert "DOGE" not in accepted_coins

    def test_max_signals_cap_at_two(self):
        engine = ConfluenceEngine(strict_threshold=80, max_signals=2)
        engine.update_market_sentiment("BULLISH", "BULLISH", "RISK_ON")

        # 5 strong candidates
        signals = [
            _make_test_signal("BTC", score=95),
            _make_test_signal("ETH", score=92),
            _make_test_signal("SOL", score=90),
            _make_test_signal("AVAX", score=88),
            _make_test_signal("LINK", score=85),
        ]
        raw_candidates = [{"coin": s.coin} for s in signals]

        accepted, _ = engine.evaluate_candidates(raw_candidates, signals)

        # Scanner must cap output at top 2 high-conviction signals
        assert len(accepted) <= 2
        assert accepted[0].coin == "BTC"
        assert accepted[1].coin == "ETH"

    def test_zero_signals_when_market_environment_is_weak(self):
        engine = ConfluenceEngine(strict_threshold=85, max_signals=2)
        # Global market regime is RISK_OFF
        engine.update_market_sentiment("BEARISH", "BEARISH", "RISK_OFF")

        signals = [
            _make_test_signal("BTC", score=90),
            _make_test_signal("ETH", score=88),
        ]
        raw_candidates = [{"coin": s.coin} for s in signals]

        accepted, results = engine.evaluate_candidates(raw_candidates, signals)

        # Zero signals accepted during bad market conditions
        assert len(accepted) == 0
        for r in results:
            assert r.accepted is False

    def test_confluence_breakdown_attached_to_signal(self):
        engine = ConfluenceEngine(strict_threshold=80, max_signals=2)
        engine.update_market_sentiment("BULLISH", "BULLISH", "RISK_ON")

        sig = _make_test_signal("SOL", score=92)
        accepted, _ = engine.evaluate_candidates([{"coin": "SOL"}], [sig])

        assert len(accepted) == 1
        b = accepted[0].confluence_breakdown
        assert b is not None
        assert "chart_score" in b
        assert "indicator_score" in b
        assert "sentiment_score" in b
        assert "news_score" in b
        assert "confluence_score" in b
        assert b["high_conviction_count"] == 1


class TestDeduplicationAndPrecision:
    def test_dedup_key_and_filter(self):
        from scanner.signal_filter import _dedup_key, deduplicate

        sig1 = _make_test_signal("BTC", score=90)
        sig1.source_bot = "VCP"
        assert _dedup_key(sig1) == "BTC::VCP"

        known = {"BTC::VCP"}
        new_sigs, new_keys = deduplicate([sig1], known)
        assert len(new_sigs) == 0
        assert len(new_keys) == 0

        sig2 = _make_test_signal("ETH", score=90)
        sig2.source_bot = "VCP"
        new_sigs2, new_keys2 = deduplicate([sig2], known)
        assert len(new_sigs2) == 1
        assert new_sigs2[0].coin == "ETH"
        assert new_keys2 == ["ETH::VCP"]

    def test_precision_rules_and_round_qty(self):
        from execution.trading.precision_rules import get_pair_spec, round_qty

        # BTC micro-lot
        btc_qty = round_qty("BTC/INR", 0.00002439)
        assert btc_qty == 0.00002
        assert btc_qty > 0

        # ZEC pair lookup & micro-lot
        spec_zec = get_pair_spec("ZEC/INR")
        assert spec_zec.lot_step_decimals >= 4
        zec_qty = round_qty("ZEC/INR", 0.0004912)
        assert zec_qty == 0.0004
        assert zec_qty > 0

        # Fallback pair with small qty
        custom_qty = round_qty("CUSTOM_COIN/INR", 0.000015)
        assert custom_qty == 0.000015
        assert custom_qty > 0

    def test_format_qty_and_telegram_alerts(self):
        from telegram.formatters import (
            format_qty,
            format_signal_ai_alert,
            format_telegram_orders,
        )

        assert format_qty(0.00002) == "0.00002"
        assert format_qty(0.00049) == "0.00049"
        assert format_qty(0.0) == "0"
        assert format_qty(1.500) == "1.5"
        assert format_qty(10.0) == "10"

        # AI alert formatting with metadata
        ai_payload = {
            "coin": "BNB",
            "recommendation": "APPROVE",
            "confidence_score": 92,
            "trend_evaluation": "BULLISH_CONTINUATION",
            "setup_quality": "HIGH_PROBABILITY_BREAKOUT",
            "supporting_factors": ["Multi-timeframe EMA alignment", "Volume expansion"],
            "risk_factors": ["Overhead resistance at ₹55,000"],
        }
        alert = format_signal_ai_alert(ai_payload)
        assert "Trend:</b> BULLISH_CONTINUATION" in alert
        assert "Setup:</b> HIGH_PROBABILITY_BREAKOUT" in alert
        assert "N/A" not in alert

        # Orders formatting
        orders = [
            {
                "coin": "BTC",
                "side": "BUY",
                "qty": 0.00002,
                "price": 8200000.0,
                "mode": "PAPER",
                "status": "FILLED",
            }
        ]
        orders_text = format_telegram_orders(orders)
        assert "BUY</code> 0.00002 @" in orders_text
        assert "BUY 0.0 @" not in orders_text

        # Trades formatting - test no +- sign collision
        from telegram.formatters import format_telegram_trades

        trades = [
            {
                "coin": "BTC",
                "bot": "STE",
                "pnl": 0.0,
                "pnl_pct": -3.58,
                "exit_reason": "STOP_LOSS",
            },
            {
                "coin": "SOL",
                "bot": "STE",
                "pnl": 15.2,
                "pnl_pct": 5.20,
                "exit_reason": "TAKE_PROFIT",
            },
        ]
        trades_text = format_telegram_trades(trades)
        assert "+-" not in trades_text
        assert "(-3.58%)" in trades_text
        assert "(+5.20%)" in trades_text


class TestPostExitCooldownAndSignalLifecycle:
    @pytest.mark.anyio
    async def test_position_close_triggers_cooldown_and_suppresses_reentry(self):
        """Verify position close sets cooldown and suppresses immediate same-coin re-entry."""
        from unittest.mock import AsyncMock

        from core.bus.event_bus import EventBus
        from core.bus.event_types import EventType
        from core.config import AppConfig
        from scanner.service import ScannerService

        bus = EventBus()
        config = AppConfig(
            v2_post_exit_cooldown_seconds=900,
            v2_scanner_strict_confluence_threshold=80,
        )

        signal_repo = AsyncMock()
        event_log = AsyncMock()

        scanner = ScannerService(
            bus=bus,
            signal_repo=signal_repo,
            event_log_repo=event_log,
            config=config,
        )

        # Simulate position closed for ZEC
        now = datetime.now(timezone.utc)
        await scanner.on_position_closed(
            EventType.POSITION_CLOSED,
            {
                "coin": "ZEC",
                "pair": "ZEC/INR",
                "bot": "STE",
                "exit_reason": "STOP_LOSS",
                "exit_price": 99614.4,
                "closed_at": now.isoformat(),
            },
        )

        assert "ZEC" in scanner._cooldowns
        assert scanner._cooldowns["ZEC"]["exit_reason"] == "STOP_LOSS"

        # Mock candidates returning ZEC and BTC
        sig_zec = _make_test_signal("ZEC", score=92)
        sig_btc = _make_test_signal("BTC", score=90)

        scanner._confluence_engine.evaluate_candidates = (
            lambda raw_candidates, signals=None, *args, **kwargs: (
                [sig_zec, sig_btc],
                [],
            )
        )
        scanner._fetch_v1_signals = AsyncMock(
            return_value=[{"coin": "ZEC", "score": 92}, {"coin": "BTC", "score": 90}]
        )
        scanner._market_context_service.refresh_market_context = AsyncMock(
            return_value={
                "btc_trend": "BULLISH",
                "eth_trend": "BULLISH",
                "market_regime": "RISK_ON",
                "fear_and_greed": 70,
            }
        )
        scanner._news_risk_service.fetch_latest_news = AsyncMock()

        # Run poll
        summary = await scanner.poll()

        # ZEC should be suppressed by cooldown; BTC should pass as new signal
        assert summary["new_signals"] == 1
        assert "BTC" in [s.coin for s in scanner._live.values()]
        assert "ZEC" not in [s.coin for s in scanner._live.values()]

    def test_capital_guard_rejects_trade_during_cooldown(self):
        """Verify CapitalGuard defense-in-depth rejects in-flight trades during post-exit cooldown."""
        from core.config import AppConfig
        from core.types import BotName
        from execution.risk.capital_guard import CapitalGuard

        config = AppConfig(v2_post_exit_cooldown_seconds=900)
        guard = CapitalGuard(config)

        now = datetime.now(timezone.utc)
        cooldowns = {
            "ZEC": {
                "exit_time": now - timedelta(seconds=60),
                "exit_reason": "STOP_LOSS",
            }
        }

        # Trade on ZEC during cooldown should be denied
        decision_zec = guard.check_trade(
            bot=BotName.STE,
            requested_amount=200.0,
            current_bot_deployed=0.0,
            total_deployed=0.0,
            current_bot_positions=0,
            current_coin="ZEC",
            cooldowns=cooldowns,
        )
        assert decision_zec.allowed is False
        assert decision_zec.code == "OPPORTUNITY_IN_COOLDOWN"
        assert "cooldown" in decision_zec.reason.lower()

        # Trade on BTC (not in cooldown) should be allowed
        decision_btc = guard.check_trade(
            bot=BotName.STE,
            requested_amount=200.0,
            current_bot_deployed=0.0,
            total_deployed=0.0,
            current_bot_positions=0,
            current_coin="BTC",
            cooldowns=cooldowns,
        )
        assert decision_btc.allowed is True

    @pytest.mark.anyio
    async def test_cooldown_expiry_allows_new_opportunity(self):
        """Verify that after cooldown window expires, a new scanner opportunity can generate a signal."""
        from unittest.mock import AsyncMock

        from core.bus.event_bus import EventBus
        from core.config import AppConfig
        from scanner.service import ScannerService

        bus = EventBus()
        config = AppConfig(
            v2_post_exit_cooldown_seconds=300,  # 5 min cooldown
            v2_scanner_strict_confluence_threshold=80,
        )

        signal_repo = AsyncMock()
        event_log = AsyncMock()

        scanner = ScannerService(
            bus=bus,
            signal_repo=signal_repo,
            event_log_repo=event_log,
            config=config,
        )

        # Record cooldown from 10 minutes ago (> 300s)
        old_exit = datetime.now(timezone.utc) - timedelta(seconds=600)
        scanner._cooldowns["ZEC"] = {
            "exit_time": old_exit,
            "exit_reason": "STOP_LOSS",
            "price": 99000.0,
        }

        sig_zec = _make_test_signal("ZEC", score=92)
        scanner._confluence_engine.evaluate_candidates = (
            lambda raw_candidates, signals=None, *args, **kwargs: ([sig_zec], [])
        )
        scanner._fetch_v1_signals = AsyncMock(
            return_value=[{"coin": "ZEC", "score": 92}]
        )
        scanner._market_context_service.refresh_market_context = AsyncMock(
            return_value={
                "btc_trend": "BULLISH",
                "eth_trend": "BULLISH",
                "market_regime": "RISK_ON",
                "fear_and_greed": 70,
            }
        )
        scanner._news_risk_service.fetch_latest_news = AsyncMock()

        summary = await scanner.poll()

        # Cooldown expired -> ZEC is accepted as a new signal
        assert summary["new_signals"] == 1
        assert "ZEC" in [s.coin for s in scanner._live.values()]
        assert "ZEC" not in scanner._cooldowns
