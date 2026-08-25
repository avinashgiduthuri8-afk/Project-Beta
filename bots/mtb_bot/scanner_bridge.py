"""
MTB scanner bridge.

Consumes PROJECT-ALPHA scanner output from either the in-process scanner module
LATEST_MTB_SIGNALS or the dashboard master endpoint /api/v1/state recent_signals.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from .config import SCANNER_API_URL, SCANNER_TIMEOUT_SECONDS

logger = logging.getLogger("mtb_bot.scanner_bridge")


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _normalize_signal(signal: dict) -> dict | None:
    coin = str(signal.get("coin") or signal.get("symbol") or "").upper().strip()
    if not coin:
        return None
    symbol = coin if coin.endswith("USDT") else f"{coin}USDT"
    price = signal.get("signal_price", signal.get("price", signal.get("entry_price", 0)))
    try:
        entry_price = float(price or 0)
    except (TypeError, ValueError):
        entry_price = 0.0
    score = signal.get("score", signal.get("confidence", 0))
    try:
        score_value = float(score or 0)
    except (TypeError, ValueError):
        score_value = 0.0
    timestamp = signal.get("timestamp") or datetime.now(timezone.utc).isoformat()
    return {
        "coin": coin.replace("USDT", ""),
        "symbol": symbol,
        "action": "BUY",
        "entry_price": entry_price,
        "score": score_value,
        "priority": signal.get("priority") or signal.get("category") or signal.get("tier") or "",
        "market_state": signal.get("market_state", ""),
        "confidence": signal.get("confidence", score_value),
        "source": "MTB_SCANNER",
        "timestamp": timestamp,
        "raw": signal,
    }


def _signals_from_module() -> list[dict]:
    try:
        from bots.scanner_bot import main as scanner_main
    except Exception:
        # NF-7: scanner module import failed (e.g. import-time error in scanner package).
        # Return [] so the bot falls back to the dashboard API path; log for operator visibility.
        logger.exception("MTB scanner module import failed — falling back to dashboard API")
        return []
    signals = getattr(scanner_main, "LATEST_MTB_SIGNALS", []) or []
    normalized = [_normalize_signal(s) for s in signals if isinstance(s, dict)]
    return [s for s in normalized if s is not None]


def _signals_from_dashboard_api() -> list[dict]:
    import os
    url = f"{SCANNER_API_URL}/api/v1/state"
    headers = {"Accept": "application/json"}
    api_key = os.getenv("DASHBOARD_API_KEY", "")
    if api_key:
        headers["X-API-Key"] = api_key
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=SCANNER_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        logger.warning("MTB scanner API fetch failed: %s", exc)
        return []
    recent = payload.get("recent_signals", []) if isinstance(payload, dict) else []
    normalized = [_normalize_signal(s) for s in recent if isinstance(s, dict)]
    return [s for s in normalized if s is not None]


def get_signals() -> list[dict]:
    module_signals = _signals_from_module()
    if module_signals:
        return module_signals
    return _signals_from_dashboard_api()


def check_scanner_health() -> bool:
    return bool(get_signals())


def signal_age_seconds(signal: dict) -> float | None:
    parsed = _parse_time(signal.get("timestamp"))
    if parsed is None:
        return None
    return (datetime.now(timezone.utc) - parsed).total_seconds()
