"""
Token Bucket Rate Limiter for Broker Order Throttling.
Prevents API bans by ensuring order requests comply with per-second and per-minute rate limits.
"""

from __future__ import annotations

import threading
import time


class RateLimiter:
    """
    Thread-safe token bucket rate limiter.
    """

    def __init__(self, rate: float = 5.0, burst: int = 10) -> None:
        """
        Args:
            rate: Token generation rate per second.
            burst: Maximum burst capacity of bucket.
        """
        self.rate = rate
        self.burst = burst
        self.tokens = float(burst)
        self.last_update = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, tokens: int = 1, blocking: bool = True, timeout: float = 2.0) -> bool:
        """
        Acquire tokens to proceed with an order.
        """
        start_time = time.monotonic()
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self.last_update
                self.last_update = now
                self.tokens = min(self.burst, self.tokens + elapsed * self.rate)

                if self.tokens >= tokens:
                    self.tokens -= tokens
                    return True

            if not blocking:
                return False

            if time.monotonic() - start_time > timeout:
                return False

            time.sleep(0.05)
