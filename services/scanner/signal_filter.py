"""
V2 ScannerService — signal filtering and deduplication.
"""

from __future__ import annotations

from datetime import datetime, timezone

from core.logging import get_logger
from core.types import Priority, Signal

logger = get_logger("scanner.signal_filter")


def filter_by_priority(signals: list[Signal], min_priority: Priority) -> list[Signal]:
    """Keep only signals at or above *min_priority*."""
    return [s for s in signals if s.priority.gte(min_priority)]


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
    current_time: datetime | None = None,
) -> tuple[list[Signal], list[Signal]]:
    """
    Partition *live_signals* into still-live and newly-expired.

    Returns: (still_live, newly_expired)
    """
    now = current_time or datetime.now(timezone.utc)
    still_live = [s for s in live_signals if s.expires_at > now]
    newly_expired = [s for s in live_signals if s.expires_at <= now]
    return still_live, newly_expired


def _dedup_key(sig: Signal) -> str:
    bot = sig.source_bot or "scanner_v1"
    return f"{sig.coin.upper()}::{bot}"
