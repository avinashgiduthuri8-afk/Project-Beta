"""
Scanner Center V2 — additive, in-memory-only observability metrics.

IMPORTANT: This module is pure instrumentation. It never influences scanner
behaviour, signal generation, or trading decisions. All state here is
transient (reset on process restart) and safe to fail — every public
function swallows its own errors so a bug here can never break the scanner
loop or the HTTP endpoints that already exist.

Exposed via GET /api/v1/scanner/monitoring (additive endpoint, does not
touch any existing route's response shape).
"""

from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Optional

_LOCK = threading.Lock()

# ---------------------------------------------------------------------------
# API call monitoring (every outbound HTTP call to CoinDCX goes through here)
# ---------------------------------------------------------------------------
API_METRICS: dict = {
    "total_calls": 0,
    "success": 0,
    "errors_429": 0,
    "errors_other": 0,
    "retries": 0,
    "fallbacks": 0,
}
_CALL_TIMESTAMPS: deque = deque(maxlen=5000)  # monotonic timestamps, for calls/min

# ---------------------------------------------------------------------------
# Cache monitoring (ticker / candle / watchlist in-memory caches)
# ---------------------------------------------------------------------------
CACHE_METRICS: dict = {
    "ticker_hits": 0, "ticker_misses": 0,
    "candle_hits": 0, "candle_misses": 0,
    "watchlist_hits": 0, "watchlist_misses": 0,
}

# ---------------------------------------------------------------------------
# Cycle / performance monitoring
# ---------------------------------------------------------------------------
CYCLE_METRICS: dict = {
    "durations_ms": deque(maxlen=100),
    "slowest_coin": None,   # {"coin": str, "ms": float}
    "fastest_coin": None,   # {"coin": str, "ms": float}
    "last_error": None,     # {"time": iso, "message": str}
}
_coin_timings_current_cycle: dict = {}

# ---------------------------------------------------------------------------
# Signal funnel (per most-recently-completed scan cycle)
# ---------------------------------------------------------------------------
FUNNEL: dict = {
    "coins_scanned": 0,
    "passed_volume": 0,
    "passed_trend": 0,
    "passed_score": 0,
    "signals_generated": 0,
}

# ---------------------------------------------------------------------------
# Event log — bounded ring buffer, newest appended, capped at 100
# ---------------------------------------------------------------------------
EVENT_LOG: deque = deque(maxlen=100)

_START_TIME = time.monotonic()


def _safe(fn):
    """Decorator: never let instrumentation raise into caller code."""
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            return None
    return wrapper


@_safe
def record_api_call(success: bool = True, status_code: Optional[int] = None,
                     retry: bool = False, fallback: bool = False) -> None:
    with _LOCK:
        API_METRICS["total_calls"] += 1
        _CALL_TIMESTAMPS.append(time.monotonic())
        if success:
            API_METRICS["success"] += 1
        elif status_code == 429:
            API_METRICS["errors_429"] += 1
        else:
            API_METRICS["errors_other"] += 1
        if retry:
            API_METRICS["retries"] += 1
        if fallback:
            API_METRICS["fallbacks"] += 1


@_safe
def calls_per_minute() -> int:
    cutoff = time.monotonic() - 60
    with _LOCK:
        while _CALL_TIMESTAMPS and _CALL_TIMESTAMPS[0] < cutoff:
            _CALL_TIMESTAMPS.popleft()
        return len(_CALL_TIMESTAMPS)


@_safe
def record_cache(kind: str, hit: bool) -> None:
    key = f"{kind}_{'hits' if hit else 'misses'}"
    with _LOCK:
        if key in CACHE_METRICS:
            CACHE_METRICS[key] += 1


@_safe
def record_coin_timing(coin: str, duration_ms: float) -> None:
    with _LOCK:
        _coin_timings_current_cycle[coin] = duration_ms


@_safe
def record_cycle_complete(duration_ms: float, error: Optional[str] = None) -> None:
    with _LOCK:
        CYCLE_METRICS["durations_ms"].append(duration_ms)
        if _coin_timings_current_cycle:
            slowest_coin = max(_coin_timings_current_cycle, key=_coin_timings_current_cycle.get)
            fastest_coin = min(_coin_timings_current_cycle, key=_coin_timings_current_cycle.get)
            CYCLE_METRICS["slowest_coin"] = {
                "coin": slowest_coin, "ms": round(_coin_timings_current_cycle[slowest_coin], 1)
            }
            CYCLE_METRICS["fastest_coin"] = {
                "coin": fastest_coin, "ms": round(_coin_timings_current_cycle[fastest_coin], 1)
            }
            _coin_timings_current_cycle.clear()
        if error:
            CYCLE_METRICS["last_error"] = {
                "time": datetime.now(timezone.utc).isoformat(), "message": str(error)[:300],
            }


@_safe
def record_funnel(coins_scanned: int = 0, passed_volume: int = 0,
                   passed_trend: int = 0, passed_score: int = 0,
                   signals_generated: int = 0) -> None:
    with _LOCK:
        FUNNEL.update({
            "coins_scanned": max(0, coins_scanned),
            "passed_volume": max(0, passed_volume),
            "passed_trend": max(0, passed_trend),
            "passed_score": max(0, passed_score),
            "signals_generated": max(0, signals_generated),
        })


@_safe
def log_event(text: str, level: str = "info") -> None:
    with _LOCK:
        EVENT_LOG.append({
            "time": datetime.now(timezone.utc).strftime("%H:%M:%S"),
            "text": str(text)[:200],
            "level": level,
        })


@_safe
def uptime_seconds() -> float:
    return time.monotonic() - _START_TIME


def snapshot() -> dict:
    """Read-only projection of all monitoring metrics. Never raises."""
    try:
        with _LOCK:
            durations = list(CYCLE_METRICS["durations_ms"])
            api = dict(API_METRICS)
            cache = dict(CACHE_METRICS)
            funnel = dict(FUNNEL)
            event_log = list(EVENT_LOG)[::-1]  # newest first
            slowest_coin = CYCLE_METRICS["slowest_coin"]
            fastest_coin = CYCLE_METRICS["fastest_coin"]
            last_error = CYCLE_METRICS["last_error"]

        api["calls_per_minute"] = calls_per_minute() or 0

        ticker_total = cache.get("ticker_hits", 0) + cache.get("ticker_misses", 0)
        candle_total = cache.get("candle_hits", 0) + cache.get("candle_misses", 0)
        watchlist_total = cache.get("watchlist_hits", 0) + cache.get("watchlist_misses", 0)
        cache["ticker_hit_ratio"] = round(cache.get("ticker_hits", 0) / ticker_total * 100, 1) if ticker_total else 0.0
        cache["candle_hit_ratio"] = round(cache.get("candle_hits", 0) / candle_total * 100, 1) if candle_total else 0.0
        cache["watchlist_hit_ratio"] = round(cache.get("watchlist_hits", 0) / watchlist_total * 100, 1) if watchlist_total else 0.0

        cycles = {
            "avg_ms": round(sum(durations) / len(durations), 1) if durations else 0,
            "max_ms": round(max(durations), 1) if durations else 0,
            "min_ms": round(min(durations), 1) if durations else 0,
            "samples": len(durations),
            "slowest_coin": slowest_coin,
            "fastest_coin": fastest_coin,
            "last_error": last_error,
        }

        return {
            "api": api,
            "cache": cache,
            "cycles": cycles,
            "funnel": funnel,
            "event_log": event_log,
            "uptime_seconds": round(uptime_seconds() or 0, 1),
        }
    except Exception as e:
        return {
            "api": {}, "cache": {}, "cycles": {}, "funnel": {},
            "event_log": [], "uptime_seconds": 0, "error": str(e),
        }
