"""Token-bucket rate limiter for broker API order throttling."""

from __future__ import annotations

import time
import threading


class RateLimiter:
    """Thread-safe Token Bucket Rate Limiter (e.g. 5 orders per second)."""

    def __init__(self, rate: float = 5.0, capacity: float = 5.0):
        self.rate = rate              # Tokens added per second
        self.capacity = capacity      # Max bucket capacity
        self.tokens = capacity
        self.last_update = time.monotonic()
        self.lock = threading.Lock()

    def acquire(self, tokens: float = 1.0, blocking: bool = True, timeout: float = 2.0) -> bool:
        """Attempt to consume tokens. Blocks if rate limit is reached."""
        start_time = time.monotonic()
        with self.lock:
            while True:
                now = time.monotonic()
                elapsed = now - self.last_update
                self.last_update = now
                self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)

                if self.tokens >= tokens:
                    self.tokens -= tokens
                    return True

                if not blocking or (time.monotonic() - start_time) >= timeout:
                    return False

                # Sleep needed time for remaining tokens
                sleep_time = (tokens - self.tokens) / self.rate
                time.sleep(min(sleep_time, 0.05))
