"""Signal Lifecycle Manager (Project-Beta)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from core.models import ScannerCandidate

logger = logging.getLogger(__name__)


class SignalState:
    NEW = "NEW"
    ACTIVE = "ACTIVE"
    TRIGGERED = "TRIGGERED"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"


class SignalLifecycleManager:
    """Tracks signal lifecycles, prunes stale setups, and manages deduplication."""

    def __init__(self, max_signal_age_minutes: int = 120):
        self.active_signals: Dict[str, Dict[str, Any]] = {}
        self.max_signal_age_minutes = max_signal_age_minutes

    def register_signal(self, candidate: ScannerCandidate) -> None:
        self.active_signals[candidate.symbol] = {
            "candidate": candidate,
            "state": SignalState.NEW,
            "registered_at": datetime.now(),
            "last_updated": datetime.now(),
        }
        logger.info(f"[Lifecycle] Registered new signal: {candidate.symbol} ({candidate.setup_type.value})")

    def prune_expired_signals(self) -> List[str]:
        now = datetime.now()
        expired = []
        for symbol, data in list(self.active_signals.items()):
            age = (now - data["registered_at"]).total_seconds() / 60.0
            if age > self.max_signal_age_minutes:
                expired.append(symbol)
                del self.active_signals[symbol]
        return expired
