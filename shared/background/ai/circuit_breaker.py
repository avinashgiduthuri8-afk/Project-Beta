"""
V2 Gemini AI Circuit Breaker.

Provides fast-fail protection when Gemini API experiences outages or degradation.
Prevents pipeline latency spikes by skipping network calls during OPEN state.

States:
  - CLOSED: Normal operation. All requests forwarded to Gemini API.
  - OPEN: Tripped after consecutive failures. Network calls skipped; immediate heuristic fallback.
  - HALF_OPEN: Cooldown expired. Allows a single trial request to probe service recovery.
"""

from __future__ import annotations

import enum
import time
from typing import Any

from core.logging import get_logger

logger = get_logger("background.ai.circuit_breaker")


class CircuitState(str, enum.Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    """
    Thread-safe and async-safe state machine for AI service availability.
    """

    def __init__(
        self,
        threshold: int = 3,
        cooldown_seconds: float = 60.0,
    ) -> None:
        self.threshold = max(1, threshold)
        self.cooldown_seconds = max(0.01, cooldown_seconds)

        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._last_failure_time: float | None = None
        self._probe_in_flight: bool = False
        self._total_trips = 0

    @property
    def state(self) -> CircuitState:
        # Check if cooldown has elapsed while in OPEN state to enter HALF_OPEN
        if self._state == CircuitState.OPEN and self._last_failure_time is not None:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self.cooldown_seconds:
                self._state = CircuitState.HALF_OPEN
                self._probe_in_flight = False
                logger.info(
                    "AI Circuit Breaker entering HALF_OPEN probe state after %.1fs cooldown",
                    elapsed,
                )
        return self._state

    def allow_request(self) -> bool:
        """
        Determine if an outgoing request to Gemini is permitted.
        Returns True if permitted, False if circuit is OPEN.
        """
        current_state = self.state

        if current_state == CircuitState.CLOSED:
            return True

        if current_state == CircuitState.HALF_OPEN:
            # Allow exactly one probe request through
            if not self._probe_in_flight:
                self._probe_in_flight = True
                logger.info(
                    "AI Circuit Breaker permitting trial probe request in HALF_OPEN state"
                )
                return True
            return False

        # OPEN state
        return False

    def record_success(self) -> bool:
        """
        Record a successful Gemini API evaluation.
        Returns True if the circuit closed as a result (recovery).
        """
        closed_now = False
        if self._state == CircuitState.HALF_OPEN:
            logger.info(
                "AI Circuit Breaker trial probe succeeded: closing circuit breaker"
            )
            self._state = CircuitState.CLOSED
            closed_now = True

        self._consecutive_failures = 0
        self._probe_in_flight = False
        return closed_now

    def record_failure(self, error: str | None = None) -> bool:
        """
        Record a failed Gemini API attempt.
        Returns True if the circuit opened as a result of this failure.
        """
        now = time.monotonic()
        self._last_failure_time = now
        self._consecutive_failures += 1
        opened_now = False

        if self._state == CircuitState.HALF_OPEN:
            logger.warning(
                "AI Circuit Breaker trial probe failed; reopening circuit for %.1fs cooldown: %s",
                self.cooldown_seconds,
                error or "Unknown error",
            )
            self._state = CircuitState.OPEN
            self._probe_in_flight = False
            self._total_trips += 1
            opened_now = True

        elif self._state == CircuitState.CLOSED:
            if self._consecutive_failures >= self.threshold:
                logger.error(
                    "AI Circuit Breaker TRIPPED to OPEN after %d consecutive failures (cooldown: %.1fs): %s",
                    self._consecutive_failures,
                    self.cooldown_seconds,
                    error or "Threshold exceeded",
                )
                self._state = CircuitState.OPEN
                self._total_trips += 1
                opened_now = True
            else:
                logger.warning(
                    "AI evaluation failure %d/%d before tripping breaker: %s",
                    self._consecutive_failures,
                    self.threshold,
                    error or "Unknown error",
                )

        return opened_now

    def get_state(self) -> dict[str, Any]:
        """Return telemetry dictionary for health checks and operator visibility."""
        curr_state = self.state
        time_until_probe = 0.0
        if curr_state == CircuitState.OPEN and self._last_failure_time is not None:
            remaining = self.cooldown_seconds - (
                time.monotonic() - self._last_failure_time
            )
            time_until_probe = round(max(0.0, remaining), 1)

        return {
            "state": curr_state.value,
            "consecutive_failures": self._consecutive_failures,
            "threshold": self.threshold,
            "cooldown_seconds": self.cooldown_seconds,
            "time_until_next_probe": time_until_probe,
            "total_trips": self._total_trips,
            "probe_in_flight": self._probe_in_flight,
        }
