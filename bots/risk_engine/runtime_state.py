"""
PROJECT-ALPHA Risk Engine — runtime trading toggle.

Lets the dashboard flip the global TRADING_ENABLED kill-switch at runtime
without needing an env var change + restart. The override is persisted to
disk so it survives process restarts; when no override has ever been set,
callers fall back to the TRADING_ENABLED env var from config.py.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Optional

from .config import TRADING_ENABLED as _ENV_TRADING_ENABLED

logger = logging.getLogger("risk_engine")

_STATE_FILE = Path(__file__).parent / "runtime_state.json"
_state_lock = threading.Lock()


def _read_state() -> dict:
    try:
        if _STATE_FILE.exists():
            with open(_STATE_FILE, "r") as f:
                return json.load(f)
    except Exception as exc:
        logger.warning("Risk engine could not read runtime_state.json: %s", exc)
    return {}


def _write_state(state: dict) -> None:
    with _state_lock:
        try:
            tmp = _STATE_FILE.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(state, f)
            tmp.replace(_STATE_FILE)
        except Exception as exc:
            logger.warning("Risk engine could not write runtime_state.json: %s", exc)


def is_trading_enabled() -> bool:
    """Return the effective TRADING_ENABLED value.

    Uses the persisted dashboard override when present, otherwise falls
    back to the TRADING_ENABLED environment variable.
    """
    state = _read_state()
    override: Optional[bool] = state.get("trading_enabled_override")
    if override is None:
        return _ENV_TRADING_ENABLED
    return bool(override)


def set_trading_enabled(enabled: bool) -> bool:
    """Persist a dashboard-driven override and return the new effective value."""
    state = _read_state()
    state["trading_enabled_override"] = bool(enabled)
    _write_state(state)
    logger.info("Trading %s via dashboard toggle", "ENABLED" if enabled else "HALTED")
    return is_trading_enabled()
