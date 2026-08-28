"""PROJECT HULK Standardized Event Bus Integration (Project-Beta)."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Dict, Any, Callable, List
from collections import defaultdict

logger = logging.getLogger("HULK.EventBus")


class HulkEventTypes:
    SCANNER_CANDIDATE_FOUND = "HULK.SCANNER.CANDIDATE_FOUND"
    AI_THESIS_EVALUATED = "HULK.AI.THESIS_EVALUATED"
    TRADE_PLAN_CONSTRUCTED = "HULK.TRADE.PLAN_CONSTRUCTED"
    ORDER_SUBMITTED = "HULK.TRADE.ORDER_SUBMITTED"
    ORDER_FILLED = "HULK.TRADE.ORDER_FILLED"
    RMS_CIRCUIT_BREAKER = "HULK.RMS.CIRCUIT_BREAKER_TRIGGERED"
    POSITION_UPDATED = "HULK.POSITION.UPDATED"
    SQUARE_OFF_TRIGGERED = "HULK.SESSION.SQUARE_OFF_TRIGGERED"


class HulkEventBus:
    """Publishes structured JSON telemetry events to PROJECT HULK."""

    def __init__(self):
        self._subscribers: Dict[str, List[Callable[[Dict[str, Any]], None]]] = defaultdict(list)
        self.event_history: List[Dict[str, Any]] = []

    def subscribe(self, event_type: str, callback: Callable[[Dict[str, Any]], None]) -> None:
        self._subscribers[event_type].append(callback)

    def publish(self, event_type: str, payload: Dict[str, Any]) -> None:
        event = {
            "event_type": event_type,
            "source": "PROJECT_BETA",
            "timestamp": datetime.now().isoformat(),
            "data": payload,
        }
        self.event_history.append(event)
        if len(self.event_history) > 1000:
            self.event_history.pop(0)

        logger.info(f"[HULK EventBus] 📡 {event_type} | {payload.get('symbol', '')}")

        for callback in self._subscribers.get(event_type, []):
            try:
                callback(event)
            except Exception as e:
                logger.error(f"Error handling HULK event {event_type}: {e}")

    def get_recent_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self.event_history[-limit:]
