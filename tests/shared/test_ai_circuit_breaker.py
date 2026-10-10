"""
Unit and Integration Tests for V2 AI Intelligence Circuit Breaker.

Tests:
  1. CircuitBreaker state transitions: CLOSED -> OPEN -> HALF_OPEN -> CLOSED.
  2. Consecutive failure tripping at configurable threshold (default 3).
  3. Rejection during OPEN state with zero latency and zero network calls.
  4. Single probe request throttling in HALF_OPEN state.
  5. Recovery on probe success and re-tripping on probe failure.
  6. Integration with AIIntelligenceService:
     - Failure streak trips breaker
     - Immediate heuristic fallback during outage
     - Event publication of AI_CIRCUIT_OPENED and AI_CIRCUIT_CLOSED
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from background.ai.circuit_breaker import CircuitBreaker, CircuitState
from background.ai.service import AIIntelligenceService
from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.config import AppConfig
from core.repository.ai_repo import AIAnalysisRepository
from core.repository.db import Database
from core.repository.event_log_repo import EventLogRepository
from core.repository.signal_repo import SignalRepository
from core.types import (
    AIAnalysis,
    AIRecommendation,
    MarketState,
    OppType,
    Priority,
    RiskLevel,
    Signal,
)

# ── 1. Pure CircuitBreaker State Machine Tests ──────────────────────────────


def test_initial_state_is_closed():
    cb = CircuitBreaker(threshold=3, cooldown_seconds=5.0)
    assert cb.state == CircuitState.CLOSED
    assert cb.allow_request() is True
    status = cb.get_state()
    assert status["state"] == "CLOSED"
    assert status["consecutive_failures"] == 0


def test_trips_to_open_after_threshold():
    cb = CircuitBreaker(threshold=3, cooldown_seconds=5.0)

    # 1st failure
    opened = cb.record_failure("Timeout 1")
    assert opened is False
    assert cb.state == CircuitState.CLOSED
    assert cb.allow_request() is True

    # 2nd failure
    opened = cb.record_failure("Timeout 2")
    assert opened is False
    assert cb.state == CircuitState.CLOSED
    assert cb.allow_request() is True

    # 3rd failure -> Trips
    opened = cb.record_failure("Timeout 3")
    assert opened is True
    assert cb.state == CircuitState.OPEN
    assert cb.allow_request() is False
    assert cb.get_state()["time_until_next_probe"] > 0.0


def test_open_state_rejects_requests():
    cb = CircuitBreaker(threshold=2, cooldown_seconds=10.0)
    cb.record_failure("Err 1")
    cb.record_failure("Err 2")
    assert cb.state == CircuitState.OPEN

    # All requests rejected during cooldown
    for _ in range(5):
        assert cb.allow_request() is False


def test_transition_to_half_open_after_cooldown():
    cb = CircuitBreaker(threshold=2, cooldown_seconds=0.08)
    cb.record_failure("Err 1")
    cb.record_failure("Err 2")
    assert cb.state == CircuitState.OPEN

    # Sleep past cooldown
    time.sleep(0.12)
    assert cb.state == CircuitState.HALF_OPEN

    # First request permitted (probe)
    assert cb.allow_request() is True
    # Second concurrent request while probe in flight is blocked
    assert cb.allow_request() is False


def test_half_open_probe_success_recovers_to_closed():
    cb = CircuitBreaker(threshold=2, cooldown_seconds=0.08)
    cb.record_failure("Err 1")
    cb.record_failure("Err 2")
    time.sleep(0.12)
    assert cb.state == CircuitState.HALF_OPEN

    # Probe succeeds
    assert cb.allow_request() is True
    recovered = cb.record_success()
    assert recovered is True
    assert cb.state == CircuitState.CLOSED
    assert cb.get_state()["consecutive_failures"] == 0
    assert cb.allow_request() is True


def test_half_open_probe_failure_reopens_circuit():
    cb = CircuitBreaker(threshold=2, cooldown_seconds=0.08)
    cb.record_failure("Err 1")
    cb.record_failure("Err 2")
    time.sleep(0.12)
    assert cb.state == CircuitState.HALF_OPEN

    # Probe fails
    assert cb.allow_request() is True
    reopened = cb.record_failure("Probe connection timeout")
    assert reopened is True
    assert cb.state == CircuitState.OPEN
    assert cb.allow_request() is False


# ── 2. AIIntelligenceService Integration Tests ──────────────────────────────


@pytest.fixture
async def ai_service_env(tmp_path):
    db_path = str(tmp_path / "test_ai_cb.db")
    db = Database(db_path)
    await db.open()

    conn = db.connection
    ai_repo = AIAnalysisRepository(conn)
    signal_repo = SignalRepository(conn)
    event_repo = EventLogRepository(conn)
    bus = EventBus()

    cfg = AppConfig(
        v2_ai_enabled=True,
        gemini_api_key="fake-key-for-testing",
        v2_ai_circuit_breaker_threshold=3,
        v2_ai_circuit_breaker_cooldown_seconds=0.10,
        v2_ai_timeout_seconds=1.0,
    )

    svc = AIIntelligenceService(
        bus=bus,
        ai_repo=ai_repo,
        event_log_repo=event_repo,
        config=cfg,
    )
    await svc.start()

    yield {
        "db": db,
        "bus": bus,
        "svc": svc,
        "ai_repo": ai_repo,
        "signal_repo": signal_repo,
        "cfg": cfg,
    }

    await svc.stop()
    await db.close()


def _make_dummy_signal(coin="SOL") -> Signal:
    now = datetime.now(timezone.utc)
    return Signal(
        id=f"sig-{coin}-01",
        coin=coin,
        pair=f"{coin}/INR",
        market_state=MarketState.BULL_TREND,
        opportunity_type=OppType.MOMENTUM_TRADE,
        priority=Priority.HIGH,
        risk_level=RiskLevel.LOW,
        score=85,
        confidence=80,
        coin_class="A",
        mtf_alignment=True,
        generated_at=now,
        expires_at=now + timedelta(minutes=5),
        raw_payload={"price": 15000.0},
    )


@pytest.mark.anyio
async def test_ai_service_circuit_breaker_trip_and_fast_fail(ai_service_env):
    env = ai_service_env
    svc: AIIntelligenceService = env["svc"]
    bus: EventBus = env["bus"]

    circuit_events = []

    async def on_event(ev, data):
        circuit_events.append((ev, data))

    bus.subscribe(EventType.AI_CIRCUIT_OPENED, on_event)
    bus.subscribe(EventType.AI_CIRCUIT_CLOSED, on_event)

    # Mock client to throw exceptions on calls
    mock_client = AsyncMock()
    mock_client.evaluate_signal.side_effect = RuntimeError(
        "Gemini 503 Service Unavailable"
    )
    svc._client = mock_client

    sig = _make_dummy_signal("BTC")
    signal_repo: SignalRepository = env["signal_repo"]
    await signal_repo.insert(sig)

    # 1. First 3 calls fail and hit Gemini client
    for _ in range(3):
        res = await svc.evaluate_signal(sig)
        assert res is not None
        assert res.recommendation in [
            AIRecommendation.APPROVE,
            AIRecommendation.REJECT,
            AIRecommendation.SCALE_DOWN,
            AIRecommendation.WATCH,
        ]

    assert mock_client.evaluate_signal.call_count == 3
    assert svc.circuit_breaker.state == CircuitState.OPEN

    # Breaker event published
    await asyncio.sleep(0.02)
    assert len(circuit_events) == 1
    assert circuit_events[0][0] == EventType.AI_CIRCUIT_OPENED

    # 2. Fourth call: breaker is OPEN -> Gemini client MUST NOT be called!
    res_fast = await svc.evaluate_signal(sig)
    assert res_fast is not None
    assert mock_client.evaluate_signal.call_count == 3  # Still 3! Not called.

    # Check health reporting
    health = svc.get_health()
    assert health["circuit_breaker"]["state"] == "OPEN"
    assert health["circuit_breaker"]["consecutive_failures"] == 3

    # 3. Wait for cooldown to transition to HALF_OPEN
    await asyncio.sleep(0.15)
    assert svc.circuit_breaker.state == CircuitState.HALF_OPEN

    # 4. Probe call succeeds
    mock_analysis = AIAnalysis(
        id="ai-test-recovered",
        signal_id=sig.id,
        coin=sig.coin,
        pair=sig.pair,
        recommendation=AIRecommendation.APPROVE,
        confidence_score=90,
        trend_evaluation="BULLISH",
        momentum_evaluation="STRONG",
        volume_evaluation="HIGH",
        setup_quality="EXCELLENT",
        market_regime="TRENDING",
        risk_reward_assessment="FAVORABLE",
        supporting_factors=["volume", "trend"],
        model_name="gemini-2.5-flash",
        execution_latency_ms=12.5,
        analyzed_at=datetime.now(timezone.utc),
    )
    mock_client.evaluate_signal.side_effect = None
    mock_client.evaluate_signal.return_value = mock_analysis

    res_probe = await svc.evaluate_signal(sig)
    assert res_probe.id == "ai-test-recovered"
    assert mock_client.evaluate_signal.call_count == 4
    assert svc.circuit_breaker.state == CircuitState.CLOSED

    # Circuit closed event published
    await asyncio.sleep(0.02)
    assert len(circuit_events) == 2
    assert circuit_events[1][0] == EventType.AI_CIRCUIT_CLOSED
