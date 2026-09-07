"""Hardware Circuit Breaker and Fallback Evaluator for AI Thesis Validation."""

from __future__ import annotations

import time
import logging
from enum import Enum
from typing import Dict, Any, Optional, Callable

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED = "CLOSED"        # Normal operation: Network LLM calls enabled
    OPEN = "OPEN"            # Tripped: Short-circuit LLM calls with 0ms penalty
    HALF_OPEN = "HALF_OPEN"  # Testing: Cooldown expired, probe single request


class CircuitBreaker:
    """State machine protecting system against LLM timeouts / API failures."""

    def __init__(
        self,
        failure_threshold: int = 3,
        cooldown_seconds: float = 60.0,
    ):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_state_change = time.time()

    def record_success(self) -> None:
        """Call on successful API execution."""
        self.failure_count = 0
        if self.state != CircuitState.CLOSED:
            logger.info("CircuitBreaker reset to CLOSED state after successful probe.")
            self.state = CircuitState.CLOSED
            self.last_state_change = time.time()

    def record_failure(self) -> None:
        """Call on API exception or timeout."""
        self.failure_count += 1
        logger.warning(f"CircuitBreaker failure recorded ({self.failure_count}/{self.failure_threshold})")

        if self.failure_count >= self.failure_threshold and self.state == CircuitState.CLOSED:
            logger.error("CircuitBreaker TRIP! Switching to OPEN state.")
            self.state = CircuitState.OPEN
            self.last_state_change = time.time()

    def allow_execution(self) -> bool:
        """Returns True if LLM request should proceed, False if short-circuited."""
        now = time.time()

        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            if now - self.last_state_change >= self.cooldown_seconds:
                logger.info("CircuitBreaker cooldown expired. Switching to HALF_OPEN state.")
                self.state = CircuitState.HALF_OPEN
                self.last_state_change = now
                return True
            return False

        if self.state == CircuitState.HALF_OPEN:
            return True

        return False


class FallbackEvaluator:
    """Deterministic rule-based evaluator used when CircuitBreaker is OPEN."""

    @staticmethod
    def evaluate_setup(symbol: str, confluence_score: float, ltp: float) -> Dict[str, Any]:
        """Evaluates trade setup deterministically with 0ms network latency."""
        confirmed = confluence_score >= 85.0
        return {
            "verdict": "CONFIRMED" if confirmed else "REJECTED",
            "confidence": 0.85 if confirmed else 0.40,
            "thesis": f"Rule-based fallback evaluation for {symbol} (Confluence Score: {confluence_score:.1f})",
            "is_fallback": True,
            "latency_ms": 0.0,
        }

