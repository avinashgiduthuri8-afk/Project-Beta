"""
V2 MetricsCollector — in-memory counters, rates, and latency distributions.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any


class MetricsCollector:
    """Collects system event counters, error tallies, and latency histograms."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = defaultdict(int)
        self._latencies: dict[str, list[float]] = defaultdict(list)
        self._max_history = 200
        self._start_time = time.monotonic()

    def increment(self, metric: str, amount: int = 1) -> None:
        self._counters[metric] += amount

    def record_latency(self, metric: str, latency_ms: float) -> None:
        buf = self._latencies[metric]
        buf.append(round(latency_ms, 2))
        if len(buf) > self._max_history:
            buf.pop(0)

    def get_metrics(self) -> dict[str, Any]:
        uptime_sec = round(time.monotonic() - self._start_time, 1)

        latency_stats = {}
        for k, vals in self._latencies.items():
            if vals:
                latency_stats[k] = {
                    "count": len(vals),
                    "avg_ms": round(sum(vals) / len(vals), 2),
                    "min_ms": min(vals),
                    "max_ms": max(vals),
                    "last_ms": vals[-1],
                }

        return {
            "uptime_seconds": uptime_sec,
            "counters": dict(self._counters),
            "latencies": latency_stats,
        }
