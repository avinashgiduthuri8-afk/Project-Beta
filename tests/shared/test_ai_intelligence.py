"""
Unit and Integration Tests for Phase 4: AI Intelligence Layer.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import httpx
import pytest

from background.ai import (
    AIIntelligenceService,
    FallbackEvaluator,
    GeminiClient,
    build_signal_prompt,
)
from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.config import AppConfig
from core.repository.ai_repo import AIAnalysisRepository
from core.repository.db import Database
from core.repository.event_log_repo import EventLogRepository
from core.repository.signal_repo import SignalRepository
from core.types import (
    AIRecommendation,
    MarketState,
    OppType,
    Priority,
    RiskLevel,
    Signal,
)


def make_test_signal(
    coin: str = "BTC",
    score: int = 88,
    confidence: int = 85,
    market_state: MarketState = MarketState.BULL_TREND,
    opp_type: OppType = OppType.MOMENTUM_TRADE,
    priority: Priority = Priority.HIGH,
    risk_level: RiskLevel = RiskLevel.LOW,
    mtf: bool = True,
    rsi: float = 58.0,
    volume_spike: float = 1.8,
    pct_change: float = 4.5,
) -> Signal:
    now = datetime.now(timezone.utc)
    return Signal(
        id=str(uuid.uuid4()),
        coin=coin,
        pair=f"B-{coin}_USDT",
        market_state=market_state,
        opportunity_type=opp_type,
        priority=priority,
        risk_level=risk_level,
        score=score,
        confidence=confidence,
        coin_class="A",
        mtf_alignment=mtf,
        generated_at=now,
        expires_at=now + timedelta(seconds=300),
        source_bot="scanner_v1",
        raw_payload={
            "coin": coin,
            "score": score,
            "rsi": rsi,
            "volume_spike_ratio": volume_spike,
            "change_24h_pct": pct_change,
            "price": 65000.0,
        },
    )


# ── 1. Domain Types & Prompt Building Tests ───────────────────────────────────


def test_ai_recommendation_enum():
    assert AIRecommendation.APPROVE == "APPROVE"
    assert AIRecommendation.REJECT == "REJECT"
    assert AIRecommendation.SCALE_DOWN == "SCALE_DOWN"
    assert AIRecommendation.WATCH == "WATCH"


def test_build_signal_prompt_formatting():
    sig = make_test_signal(coin="ETH", score=92)
    prompt = build_signal_prompt(sig)
    assert "ETH" in prompt
    assert "92" in prompt
    assert "BULL_TREND" in prompt or "bull_trend" in prompt
    assert "indicators" in prompt


# ── 2. Fallback Evaluator Rule Tests ──────────────────────────────────────────


def test_fallback_evaluator_strong_bull_signal():
    sig = make_test_signal(
        score=90,
        market_state=MarketState.BREAKOUT,
        risk_level=RiskLevel.LOW,
        mtf=True,
        rsi=62.0,
        volume_spike=2.0,
    )
    analysis = FallbackEvaluator.evaluate(sig)
    assert analysis.recommendation == AIRecommendation.APPROVE
    assert analysis.confidence_score >= 75
    assert analysis.suggested_adjustments.get("size_multiplier") == 1.0
    assert len(analysis.supporting_factors) >= 2


def test_fallback_evaluator_moderate_signal_scales_down():
    sig = make_test_signal(
        score=72,
        market_state=MarketState.PULLBACK,
        risk_level=RiskLevel.MEDIUM,
        mtf=True,
        rsi=52.0,
        volume_spike=1.0,
    )
    analysis = FallbackEvaluator.evaluate(sig)
    assert analysis.recommendation == AIRecommendation.SCALE_DOWN
    assert analysis.suggested_adjustments.get("size_multiplier") == 0.5
    assert analysis.suggested_adjustments.get("tighten_stop") is True


def test_fallback_evaluator_downtrend_or_avoid_rejects():
    sig = make_test_signal(
        score=45,
        market_state=MarketState.DOWNTREND,
        opp_type=OppType.AVOID,
        risk_level=RiskLevel.HIGH,
        mtf=False,
    )
    analysis = FallbackEvaluator.evaluate(sig)
    assert analysis.recommendation == AIRecommendation.REJECT
    assert analysis.suggested_adjustments.get("size_multiplier") == 0.0
    assert len(analysis.risk_factors) >= 1


# ── 3. AI Repository & Database Tests ─────────────────────────────────────────


@pytest.mark.anyio
async def test_ai_repository_crud(tmp_path):
    db_path = str(tmp_path / f"test_ai_repo_{uuid.uuid4().hex[:6]}.db")
    db = Database(db_path)
    await db.open()
    try:
        conn = db.connection
        signal_repo = SignalRepository(conn)
        ai_repo = AIAnalysisRepository(conn)

        sig = make_test_signal(coin="SOL", score=85)
        await signal_repo.insert(sig)

        analysis = FallbackEvaluator.evaluate(sig)
        saved_id = await ai_repo.insert(analysis)
        assert saved_id == analysis.id

        fetched = await ai_repo.get_by_id(saved_id)
        assert fetched is not None
        assert fetched.coin == "SOL"
        assert fetched.recommendation == analysis.recommendation
        assert fetched.confidence_score == analysis.confidence_score
        assert len(fetched.supporting_factors) > 0

        by_sig = await ai_repo.get_by_signal_id(sig.id)
        assert by_sig is not None
        assert by_sig.id == analysis.id

        recent = await ai_repo.get_recent(limit=10)
        assert len(recent) == 1
        assert recent[0].coin == "SOL"

        counts = await ai_repo.count_by_recommendation()
        assert analysis.recommendation.value in counts
    finally:
        await db.close()


# ── 4. AI Intelligence Service & Event Bus Tests ──────────────────────────────


@pytest.mark.anyio
async def test_ai_service_evaluate_and_publishes_events(tmp_path):
    db_path = str(tmp_path / f"test_ai_service_{uuid.uuid4().hex[:6]}.db")
    db = Database(db_path)
    await db.open()
    service = None
    try:
        conn = db.connection
        bus = EventBus()
        signal_repo = SignalRepository(conn)
        ai_repo = AIAnalysisRepository(conn)
        event_log = EventLogRepository(conn)
        cfg = AppConfig(
            v2_db_path=db_path, v2_ai_enabled=True, v2_ai_confidence_threshold=70
        )

        service = AIIntelligenceService(
            bus=bus,
            ai_repo=ai_repo,
            event_log_repo=event_log,
            config=cfg,
            signal_repo=signal_repo,
        )
        await service.start()

        evaluated_events = []
        confirmed_events = []
        rejected_events = []

        async def on_eval(et, p):
            evaluated_events.append(p)

        async def on_conf(et, p):
            confirmed_events.append(p)

        async def on_rej(et, p):
            rejected_events.append(p)

        bus.subscribe(EventType.SIGNAL_AI_EVALUATED, on_eval)
        bus.subscribe(EventType.SIGNAL_AI_CONFIRMED, on_conf)
        bus.subscribe(EventType.SIGNAL_AI_REJECTED, on_rej)

        # 1. High score signal -> should confirm
        bull_sig = make_test_signal(coin="BTC", score=90, mtf=True, volume_spike=2.0)
        await signal_repo.insert(bull_sig)
        await service.evaluate_signal(bull_sig)

        assert len(evaluated_events) == 1
        assert len(confirmed_events) == 1
        assert confirmed_events[0]["coin"] == "BTC"
        assert confirmed_events[0]["recommendation"] in ("APPROVE", "SCALE_DOWN")

        # 2. Downtrend signal -> should reject
        bear_sig = make_test_signal(
            coin="DOGE",
            score=40,
            market_state=MarketState.DOWNTREND,
            opp_type=OppType.AVOID,
        )
        await signal_repo.insert(bear_sig)
        await service.evaluate_signal(bear_sig)

        assert len(evaluated_events) == 2
        assert len(rejected_events) == 1
        assert rejected_events[0]["coin"] == "DOGE"

        health = service.get_health()
        assert health["healthy"] is True
        assert health["total_evaluations"] == 2
        assert health["confirmed_count"] == 1
        assert health["rejected_count"] == 1
    finally:
        if service:
            await service.stop()
        await db.close()


@pytest.mark.anyio
async def test_ai_service_on_signal_generated_subscription(tmp_path):
    db_path = str(tmp_path / f"test_ai_sub_{uuid.uuid4().hex[:6]}.db")
    db = Database(db_path)
    await db.open()
    service = None
    try:
        conn = db.connection
        bus = EventBus()
        signal_repo = SignalRepository(conn)
        ai_repo = AIAnalysisRepository(conn)
        event_log = EventLogRepository(conn)
        cfg = AppConfig(
            v2_db_path=db_path, v2_ai_enabled=True, v2_ai_min_priority="Medium"
        )

        service = AIIntelligenceService(
            bus=bus,
            ai_repo=ai_repo,
            event_log_repo=event_log,
            config=cfg,
            signal_repo=signal_repo,
        )
        await service.start()

        sig = make_test_signal(coin="AVAX", score=82, priority=Priority.HIGH)
        await signal_repo.insert(sig)

        # Publish SIGNAL_GENERATED to bus
        await bus.publish(
            EventType.SIGNAL_GENERATED, {"signal_id": sig.id, "coin": "AVAX"}
        )
        await asyncio.sleep(0.05)

        recent = await ai_repo.get_by_coin("AVAX")
        assert len(recent) == 1
        assert recent[0].coin == "AVAX"
    finally:
        if service:
            await service.stop()
        await db.close()


# ── 5. Mocked Gemini API Client Tests ─────────────────────────────────────────


@pytest.mark.anyio
async def test_gemini_client_mock_success():
    client = GeminiClient(api_key="fake-key", model="gemini-2.5-flash")
    sig = make_test_signal(coin="NEAR", score=86)

    mock_gemini_json = {
        "recommendation": "APPROVE",
        "confidence_score": 88,
        "trend_evaluation": "Strong 4h/24h ascending channel",
        "momentum_evaluation": "RSI at 59 with bullish MACD crossover",
        "volume_evaluation": "Volume expansion of 2.1x over 20-period moving average",
        "setup_quality": "High grade momentum breakout",
        "market_regime": "bull_trend",
        "risk_reward_assessment": "Favorable 2.8:1 asymmetric profile",
        "supporting_factors": [
            "Clean resistance break",
            "Volume surge",
            "MTF confirmation",
        ],
        "conflicts": [],
        "risk_factors": ["Crypto macro headline risk"],
        "suggested_adjustments": {
            "size_multiplier": 1.2,
            "tighten_stop": False,
            "target_notes": "Aim for 3R",
        },
    }

    mock_resp = {
        "candidates": [{"content": {"parts": [{"text": json.dumps(mock_gemini_json)}]}}]
    }

    with patch("httpx.AsyncClient.post") as mock_post:
        mock_http_response = MagicMock()
        mock_http_response.status_code = 200
        mock_http_response.json.return_value = mock_resp
        mock_http_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_http_response

        analysis = await client.evaluate_signal(sig)
        assert analysis.recommendation == AIRecommendation.APPROVE
        assert analysis.confidence_score == 88
        assert analysis.model_name == "gemini-2.5-flash"
        assert analysis.suggested_adjustments["size_multiplier"] == 1.2
