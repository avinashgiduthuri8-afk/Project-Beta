"""
V2 ScannerService — V1 HTTP response → V2 Signal domain type.

The V1 scanner API returns signals in its own schema. This adapter
translates that schema into the canonical V2 Signal dataclass.

No business logic — pure field mapping and type coercion.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from v2.core.types import (
    MarketState, OppType, Priority, RiskLevel, Signal,
)
from v2.core.logging import get_logger

logger = get_logger("v2.services.scanner_service.adapter")

# V1 maps market_state → opportunity_type
_STATE_TO_OPP: dict[str, OppType] = {
    "breakout":   OppType.MOMENTUM_TRADE,
    "bull_trend": OppType.CONTINUATION,
    "pullback":   OppType.ACCUMULATION,
    "recovery":   OppType.RECOVERY_TRADE,
    "sideways":   OppType.WATCHLIST,
    "downtrend":  OppType.AVOID,
}

_PRIORITY_MAP: dict[str, Priority] = {
    "elite":  Priority.ELITE,
    "high":   Priority.HIGH,
    "medium": Priority.MEDIUM,
    "watch":  Priority.WATCH,
    "ignore": Priority.IGNORE,
}

_RISK_MAP: dict[str, RiskLevel] = {
    "low":    RiskLevel.LOW,
    "medium": RiskLevel.MEDIUM,
    "high":   RiskLevel.HIGH,
}


def _parse_bool(raw: Any) -> bool:
    """
    Explicit boolean parse — avoids `bool("none") == True` traps.
    Truthy strings: "true", "yes", "1".  Everything else is False.
    """
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int):
        return raw != 0
    if isinstance(raw, str):
        return raw.strip().lower() in ("true", "yes", "1")
    return False


def _parse_priority(raw: str | None, score: int) -> Priority:
    if raw:
        norm = raw.strip().lower()
        if norm in _PRIORITY_MAP:
            return _PRIORITY_MAP[norm]
    return Priority.from_score(score)


def _parse_risk(raw: str | None) -> RiskLevel:
    if raw:
        norm = raw.strip().lower()
        if norm in _RISK_MAP:
            return _RISK_MAP[norm]
    return RiskLevel.MEDIUM


def _parse_market_state(raw: str | None) -> MarketState:
    if raw:
        norm = raw.strip().lower()
        try:
            return MarketState(norm)
        except ValueError:
            pass
    return MarketState.SIDEWAYS


def _parse_datetime(raw: str | None) -> datetime | None:
    if not raw:
        return None
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f+00:00",
        "%Y-%m-%dT%H:%M:%S+00:00",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    logger.warning("Could not parse datetime", extra={"raw": raw})
    return None


def v1_response_to_signals(
    data: list[dict[str, Any]],
    signal_ttl_seconds: int = 300,
) -> list[Signal]:
    """
    Convert the V1 /api/v1/scanner/signals response list into V2 Signal objects.

    Signals with missing required fields are skipped with a warning.
    """
    now = datetime.now(timezone.utc)
    signals: list[Signal] = []

    for item in data:
        try:
            coin = (
                item.get("coin")
                or item.get("symbol")
                or item.get("pair", "").replace("B-", "").replace("_USDT", "")
                or ""
            )
            if not coin:
                continue

            pair = item.get("pair") or f"B-{coin}_USDT"
            score = int(item.get("score") or item.get("opportunity_score") or 0)
            market_state = _parse_market_state(item.get("market_state"))
            priority = _parse_priority(item.get("priority"), score)
            risk = _parse_risk(item.get("risk") or item.get("risk_level"))

            # opportunity_type: prefer explicit field, fall back to market_state mapping
            raw_opp = item.get("opportunity_type")
            if raw_opp:
                try:
                    opp_type = OppType(raw_opp)
                except ValueError:
                    opp_type = _STATE_TO_OPP.get(market_state.value, OppType.WATCHLIST)
            else:
                opp_type = _STATE_TO_OPP.get(market_state.value, OppType.WATCHLIST)

            confidence = int(item.get("confidence") or 0)
            coin_class = item.get("coin_class") or item.get("class")
            mtf = _parse_bool(item.get("mtf_alignment") or item.get("mtf"))

            generated_at = _parse_datetime(item.get("timestamp") or item.get("generated_at"))
            if generated_at is None:
                generated_at = now

            expires_at = generated_at + timedelta(seconds=signal_ttl_seconds)

            sig = Signal(
                id               = str(uuid.uuid4()),
                coin             = coin.upper(),
                pair             = pair,
                market_state     = market_state,
                opportunity_type = opp_type,
                priority         = priority,
                risk_level       = risk,
                score            = score,
                confidence       = confidence,
                coin_class       = coin_class,
                mtf_alignment    = mtf,
                generated_at     = generated_at,
                expires_at       = expires_at,
                source_bot       = "scanner_v1",
                raw_payload      = item,
            )
            signals.append(sig)

        except Exception as exc:
            logger.warning(
                "Skipping malformed signal",
                extra={"error": str(exc), "item": str(item)[:200]},
            )

    return signals
