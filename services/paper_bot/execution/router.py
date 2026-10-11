"""Strict paper-only event boundary retained for EventBus compatibility."""

from typing import Any

from shared.mode_guard import ModeIsolationError, validate_execution_mode


class PaperExecutionRouter:
    def __init__(self, bus: Any, auto_trader: Any, **_: Any) -> None:
        self.bus = bus
        self.auto_trader = auto_trader

    async def handle_signal_event(self, event_type: Any, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            validate_execution_mode(payload, expected_mode="PAPER", component="PaperExecutionRouter")
        except ModeIsolationError as error:
            return {"success": False, "rejected": True, "error": "MODE_ISOLATION_ERROR", "reason": str(error)}
        return {"success": True, "rejected": False, "signal_id": payload.get("id") or payload.get("signal_id")}
