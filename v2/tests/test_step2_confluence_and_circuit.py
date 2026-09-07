"""Unit tests for Step 2: C2 Confluence Engine & AI Circuit Breaker."""

import pytest
import time
from v2.services.scanner_service.confluence_engine import C2ConfluenceEngine, ConfluenceWeights
from v2.services.ai_intelligence_service.circuit_breaker import CircuitBreaker, CircuitState, FallbackEvaluator


def test_c2_confluence_scoring():
    engine = C2ConfluenceEngine()

    # Case 1: High scores across all 4 pillars -> ELITE Pass (Score >= 85)
    # Chart: 90, Indicator: 85, Regime: 85, Sentiment: 80
    # Score = (0.40*90) + (0.25*85) + (0.20*85) + (0.15*80) = 36 + 21.25 + 17 + 12 = 86.25
    res_high = engine.calculate_score(90.0, 85.0, 85.0, 80.0)
    assert res_high["total_score"] == 86.25
    assert res_high["is_elite"] is True

    # Case 2: Lower score -> Rejection (< 85)
    res_low = engine.calculate_score(60.0, 70.0, 50.0, 50.0)
    assert res_low["total_score"] < 85.0
    assert res_low["is_elite"] is False


def test_circuit_breaker_state_transitions():
    cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=0.5)

    # Initial state CLOSED
    assert cb.state == CircuitState.CLOSED
    assert cb.allow_execution() is True

    # Failures 1 & 2 -> Still CLOSED
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.CLOSED
    assert cb.allow_execution() is True

    # Failure 3 -> Trips to OPEN
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    assert cb.allow_execution() is False  # Short-circuit network calls

    # Wait for cooldown expiration
    time.sleep(0.6)

    # First call after cooldown -> HALF_OPEN state
    assert cb.allow_execution() is True
    assert cb.state == CircuitState.HALF_OPEN

    # Success in HALF_OPEN resets to CLOSED
    cb.record_success()
    assert cb.state == CircuitState.CLOSED


def test_fallback_evaluator():
    res_pass = FallbackEvaluator.evaluate_setup("RELIANCE", confluence_score=87.5, ltp=2450.0)
    assert res_pass["verdict"] == "CONFIRMED"
    assert res_pass["is_fallback"] is True
    assert res_pass["latency_ms"] == 0.0

    res_fail = FallbackEvaluator.evaluate_setup("RELIANCE", confluence_score=75.0, ltp=2450.0)
    assert res_fail["verdict"] == "REJECTED"

