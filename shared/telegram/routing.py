"""Fail-closed routing primitives for PAPER/LIVE Telegram notifications."""

from __future__ import annotations

from typing import Any

from core.logging import get_logger

logger = get_logger("telegram.routing")

VALID_NOTIFICATION_MODES = frozenset({"PAPER", "LIVE"})


def notification_mode(
    payload: dict[str, Any] | None,
    *,
    expected_mode: str | None = None,
) -> str | None:
    """Return an explicit valid mode, otherwise reject and log the event."""
    raw_mode = payload.get("mode") if isinstance(payload, dict) else None
    mode = str(raw_mode or "").strip().upper()
    expected = str(expected_mode or "").strip().upper() or None

    if mode not in VALID_NOTIFICATION_MODES:
        logger.warning(
            "Telegram notification rejected: missing or invalid mode",
            extra={"mode": mode or None, "expected_mode": expected},
        )
        return None
    if expected is not None and mode != expected:
        logger.warning(
            "Telegram notification rejected: cross-environment route",
            extra={"mode": mode, "expected_mode": expected},
        )
        return None

    service = str((payload or {}).get("service") or "").strip().upper()
    if service:
        if "PAPER" in service and mode != "PAPER":
            logger.warning("Telegram notification rejected: service/mode mismatch")
            return None
        if "LIVE" in service and mode != "LIVE":
            logger.warning("Telegram notification rejected: service/mode mismatch")
            return None
    return mode
