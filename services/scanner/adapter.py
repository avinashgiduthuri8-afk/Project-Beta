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

from core.logging import get_logger
from core.types import (
    MarketState,
    OppType,
    Priority,
    RiskLevel,
    Signal,
)

logger = get_logger("scanner.adapter")

# V1 maps market_state → opportunity_type
_STATE_TO_OPP: dict[str, OppType] = {
    "breakout": OppType.MOMENTUM_TRADE,
    "bull_trend": OppType.CONTINUATION,
    "pullback": OppType.ACCUMULATION,
    "recovery": OppType.RECOVERY_TRADE,
    "sideways": OppType.WATCHLIST,
    "downtrend": OppType.AVOID,
}

_PRIORITY_MAP: dict[str, Priority] = {
    "elite": Priority.ELITE,
    "high": Priority.HIGH,
    "medium": Priority.MEDIUM,
    "watch": Priority.WATCH,
    "ignore": Priority.IGNORE,
}

_RISK_MAP: dict[str, RiskLevel] = {
    "low": RiskLevel.LOW,
    "medium": RiskLevel.MEDIUM,
    "high": RiskLevel.HIGH,
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


def raw_response_to_signals(
    data: list[dict[str, Any]],
    signal_ttl_seconds: int = 300,
    breakout_ttl_seconds: int = 300,
) -> list[Signal]:
    """
    Convert raw scanner signal responses into canonical Signal domain objects.

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
            mtf = _parse_bool(
                item.get("mtf_alignment")
                or item.get("mtf")
                or item.get("is_mtf_aligned")
            )
            if not mtf:
                reasons = item.get("reasons", [])
                if isinstance(reasons, list):
                    for r in reasons:
                        if isinstance(r, str) and (
                            "mtf" in r.lower() or "aligned" in r.lower()
                        ):
                            mtf = True
                            break

            generated_at = _parse_datetime(
                item.get("timestamp") or item.get("generated_at")
            )
            if generated_at is None:
                generated_at = now

            # Timeframe-aware TTL (B8) & Hard Breakout TTL (Phase 3A)
            # Breakout momentum signals have a strictly bounded execution window (300-600s)
            timeframe_ttl_map = {
                "5m": 300,
                "15m": 900,
                "1h": 3600,
                "1d": 86400,
            }
            tf = (
                str(item.get("timeframe") or item.get("interval") or "").strip().lower()
            )
            is_breakout = (
                market_state == MarketState.BREAKOUT
                or opp_type == OppType.MOMENTUM_TRADE
                or str(item.get("opportunity_type") or "").lower() == "momentum_trade"
            )
            if is_breakout:
                effective_ttl = breakout_ttl_seconds
            else:
                effective_ttl = timeframe_ttl_map.get(tf, signal_ttl_seconds)

            expires_at = generated_at + timedelta(seconds=effective_ttl)

            sig = Signal(
                id=str(uuid.uuid4()),
                coin=coin.upper(),
                pair=pair,
                market_state=market_state,
                opportunity_type=opp_type,
                priority=priority,
                risk_level=risk,
                score=score,
                confidence=confidence,
                coin_class=coin_class,
                mtf_alignment=mtf,
                generated_at=generated_at,
                expires_at=expires_at,
                source_bot=item.get("bot") or item.get("source_bot") or "scanner_v1",
                raw_payload=item,
            )
            signals.append(sig)

        except Exception as exc:
            logger.warning(
                "Skipping malformed signal",
                extra={"error": str(exc), "item": str(item)[:200]},
            )

    return signals


def raw_signal_to_domain(
    item: dict[str, Any],
    signal_ttl_seconds: int = 300,
    breakout_ttl_seconds: int = 300,
) -> Signal:
    """Convert a single raw signal dict into a canonical Signal domain object."""
    results = raw_response_to_signals(
        [item],
        signal_ttl_seconds=signal_ttl_seconds,
        breakout_ttl_seconds=breakout_ttl_seconds,
    )
    if results:
        return results[0]

    now = datetime.now(timezone.utc)
    coin = item.get("coin") or item.get("symbol") or "UNKNOWN"
    raw_ms = _parse_market_state(item.get("market_state"))
    raw_opp = item.get("opportunity_type")
    if raw_opp:
        try:
            opp_type = OppType(raw_opp)
        except ValueError:
            opp_type = _STATE_TO_OPP.get(raw_ms.value, OppType.WATCHLIST)
    else:
        opp_type = _STATE_TO_OPP.get(raw_ms.value, OppType.WATCHLIST)

    is_breakout = (
        raw_ms == MarketState.BREAKOUT
        or opp_type == OppType.MOMENTUM_TRADE
        or str(raw_opp or "").lower() == "momentum_trade"
    )
    effective_ttl = breakout_ttl_seconds if is_breakout else signal_ttl_seconds

    return Signal(
        id=item.get("id") or item.get("signal_id") or str(uuid.uuid4()),
        coin=coin.upper(),
        pair=item.get("pair") or f"B-{coin}_USDT",
        market_state=raw_ms,
        opportunity_type=opp_type,
        priority=_parse_priority(item.get("priority"), int(item.get("score") or 0)),
        risk_level=_parse_risk(item.get("risk") or item.get("risk_level")),
        score=int(item.get("score") or 0),
        confidence=int(item.get("confidence") or 0),
        coin_class=item.get("coin_class") or item.get("class"),
        mtf_alignment=_parse_bool(
            item.get("mtf_alignment") or item.get("mtf") or item.get("is_mtf_aligned")
        ),
        generated_at=now,
        expires_at=now + timedelta(seconds=effective_ttl),
        source_bot=item.get("bot") or item.get("source_bot") or "scanner",
        raw_payload=item,
    )
