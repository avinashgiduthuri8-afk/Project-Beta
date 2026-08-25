"""
PROJECT-ALPHA Shared Risk Engine — configuration.

Single source of truth for capital allocation, per-bot limits,
and the global TRADING_ENABLED / EMERGENCY_STOP kill-switches.

Deny-by-default: all capital limits default to 0 (not configured).
If an env var is missing or unparseable the limit stays 0 and the
engine will return CAPITAL_LIMIT_NOT_CONFIGURED rather than trading
with an invented value.
"""

from __future__ import annotations

import logging
import os
import threading

_cfg_logger = logging.getLogger("risk_engine")


def _parse_float(env_var: str, default: float = 0.0) -> float:
    """Parse a float env var.  Returns *default* (0.0) and logs an error on
    any parse failure so misconfiguration is visible instead of silent."""
    raw = os.getenv(env_var)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        _cfg_logger.error(
            "[Config] %s=%r is not a valid number — treating as %.0f "
            "(capital limits must be set explicitly).",
            env_var, raw, default,
        )
        return default


def _parse_int(env_var: str, default: int = 0) -> int:
    """Parse an int env var.  Returns *default* and logs an error on failure."""
    raw = os.getenv(env_var)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        _cfg_logger.error(
            "[Config] %s=%r is not a valid integer — treating as %d.",
            env_var, raw, default,
        )
        return default


# ── Global kill-switches ──────────────────────────────────────────────────────
# Set TRADING_ENABLED=false to halt ALL bots (VGX, PMB, MTB).
TRADING_ENABLED: bool = os.getenv("TRADING_ENABLED", "false").lower() == "true"

# ── In-memory runtime toggle ──────────────────────────────────────────────────
# Initialised from the env-var default above; updated at runtime by the
# dashboard toggle (POST /api/v1/trading/toggle).  Intentionally NOT persisted
# to disk — the flag resets to the env-var default on every process restart.
_trading_lock: threading.Lock = threading.Lock()
_trading_enabled: bool = TRADING_ENABLED


def get_trading_enabled() -> bool:
    """Return the live in-memory TRADING_ENABLED flag."""
    with _trading_lock:
        return _trading_enabled


def set_trading_enabled(value: bool) -> bool:
    """Set the live in-memory TRADING_ENABLED flag and return its new value."""
    global _trading_enabled
    with _trading_lock:
        _trading_enabled = bool(value)
        return _trading_enabled

# Set EMERGENCY_STOP=true to immediately block any new trade across all bots.
EMERGENCY_STOP: bool = os.getenv("EMERGENCY_STOP", "false").lower() == "true"

# ── Capital allocation per bot (INR paper units) ──────────────────────────────
# Defaults are 0 (deny-by-default).  Set env vars explicitly to enable trading.
TRADE_CONFIG: dict[str, float] = {
    "VGX": _parse_float("VGX_TRADE_AMOUNT", 0.0),
    "PMB": _parse_float("PMB_TRADE_AMOUNT", 0.0),
    "MTB": _parse_float("MTB_TRADE_AMOUNT", 0.0),
}

# Aggregate limit: total deployed capital across ALL bots at any moment.
# Default 0 = not configured; engine will deny trades until set explicitly.
TOTAL_CAPITAL_LIMIT: float = _parse_float("TOTAL_CAPITAL_LIMIT", 0.0)

# Per-bot limit: max capital a single bot may have deployed simultaneously.
# Default 0 = not configured; engine will deny trades until set explicitly.
BOT_CAPITAL_LIMIT: dict[str, float] = {
    "VGX": _parse_float("VGX_CAPITAL_LIMIT", 0.0),
    "PMB": _parse_float("PMB_CAPITAL_LIMIT", 0.0),
    "MTB": _parse_float("MTB_CAPITAL_LIMIT", 0.0),
}

# Max open positions per bot.
MAX_POSITIONS: dict[str, int] = {
    "VGX": _parse_int("VGX_MAX_POSITIONS", 5),
    "PMB": _parse_int("PMB_MAX_POSITIONS", 5),
    "MTB": _parse_int("MTB_MAX_POSITIONS", 3),
}

# ── Startup config validation ─────────────────────────────────────────────────
# Log clearly if capital limits are not configured so operators know why
# trading is denied.  This runs once at import time.
if TOTAL_CAPITAL_LIMIT == 0.0:
    _cfg_logger.error(
        "[Config] TOTAL_CAPITAL_LIMIT is 0 or not set — "
        "trading will be denied until this is configured explicitly."
    )

for _bot, _limit in BOT_CAPITAL_LIMIT.items():
    if _limit == 0.0:
        _cfg_logger.error(
            "[Config] %s_CAPITAL_LIMIT is 0 or not set — "
            "trading for %s will be denied until this is configured explicitly.",
            _bot, _bot,
        )

# Paper-mode label for each bot (LIVE / PAPER / DISABLED / PAUSED).
VALID_BOT_MODES = {"LIVE", "PAPER", "DISABLED", "PAUSED"}

_BOT_MODE_ENV = {
    "VGX": "VGX_BOT_MODE",
    "PMB": "PMB_BOT_MODE",
    "MTB": "MTB_BOT_MODE",
}

BOT_MODE: dict[str, str] = {}
for _bot, _env in _BOT_MODE_ENV.items():
    _raw = os.getenv(_env, "PAPER")
    _normalised = _raw.strip().upper()
    if _normalised not in VALID_BOT_MODES:
        _cfg_logger.warning(
            "[Config] %s=%r is not a valid BOT_MODE "
            "(allowed: %s) — clamping to DISABLED",
            _env, _raw, ", ".join(sorted(VALID_BOT_MODES)),
        )
        _normalised = "DISABLED"
    BOT_MODE[_bot] = _normalised
