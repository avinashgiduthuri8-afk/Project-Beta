"""
Project Beta paper-mode isolation safeguards.

The execution layer rejects every mode except PAPER.
"""

from typing import Any
from core.types import BotMode
from core.exceptions import BetaError


class ModeIsolationError(BetaError):
    """Raised when an invalid execution mode crosses service boundary."""
    pass


def validate_execution_mode(
    entity: Any,
    expected_mode: str | BotMode,
    component: str = "ExecutionLayer",
) -> str:
    """
    Validates that entity (Signal, Order, Position, Trade, or dict) contains a valid mode
    matching the expected PAPER mode.

    Raises ModeIsolationError if mode is missing, invalid, or does not match expected_mode.
    """
    expected_str = (
        expected_mode.value if isinstance(expected_mode, BotMode) else str(expected_mode)
    ).upper()

    if expected_str != "PAPER":
        raise ModeIsolationError(f"[{component}] Invalid service expected_mode configuration: {expected_mode}")

    mode_val: str | None = None
    if isinstance(entity, dict):
        mode_val = entity.get("mode")
    elif hasattr(entity, "mode"):
        raw_mode = getattr(entity, "mode")
        if isinstance(raw_mode, BotMode):
            mode_val = raw_mode.value
        elif raw_mode is not None:
            mode_val = str(raw_mode)

    if not mode_val:
        raise ModeIsolationError(
            f"[{component}] REJECTED: Entity mode is missing/None/invalid. Entity: {entity}"
        )

    mode_str = str(mode_val).upper()

    if mode_str != expected_str:
        raise ModeIsolationError(
            f"[{component}] REJECTED: Mode mismatch! Target service is [{expected_str}] "
            f"but received object mode is [{mode_str}]."
        )

    return mode_str
