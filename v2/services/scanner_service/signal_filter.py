"""
V2 ScannerService — signal filtering and deduplication.
"""

from __future__ import annotations

from datetime import datetime, timezone

from v2.core.types import Priority, Signal
from v2.core.logging import get_logger

logger = get_logger("v2.services.scanner_service.signal_filter")


def filter_by_priority(
    signals: list[Signal], min_priority: Priority
) -> list[Signal]:
    """Keep only signals at or above *min_priority*."""
    return [s for s in signals if s.priority.gte(min_priority)]


def filter_live(signals: list[Signal]) -> list[Signal]:
    """Keep only signals whose TTL has not elapsed."""
    return [s for s in signals if s.is_live]


def deduplicate(
    incoming: list[Signal],
    known_keys: set[str],
) -> tuple[list[Signal], list[str]]:
    """
    Split *incoming* into new signals and stale/seen signals.

    A signal is considered new if its deduplication key is not in *known_keys*.
    The key is: ``{coin}::{generated_at_isoformat}``.

    Returns:
        (new_signals, new_keys_to_add)
    """
    new_signals: list[Signal] = []
    new_keys: list[str] = []

    for sig in incoming:
        key = _dedup_key(sig)
        if key not in known_keys:
            new_signals.append(sig)
            new_keys.append(key)

    return new_signals, new_keys


def detect_expired(
    live_signals: list[Signal],
) -> tuple[list[Signal], list[Signal]]:
    """
    Partition *live_signals* into still-live and newly-expired.

    Returns: (still_live, newly_expired)
    """
    now = datetime.now(timezone.utc)
    still_live = [s for s in live_signals if s.expires_at > now]
    newly_expired = [s for s in live_signals if s.expires_at <= now]
    return still_live, newly_expired


def _dedup_key(sig: Signal) -> str:
    return f"{sig.coin}::{sig.generated_at.isoformat()}"
