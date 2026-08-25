"""
PROJECT-ALPHA PMB Bot configuration.

PMB = Price Movement Bot.
Strategy: Base buy on signal → Dip-buy on -5% drops (up to MAX_DIPS) →
          Partial-sell on every PARTIAL_SELL_TRIGGER_PCT rise.
"""

from __future__ import annotations

import os
from pathlib import Path

BOT_NAME    = "PMB"
BOT_VERSION = "1.0"
BOT_MODE    = os.getenv("PMB_BOT_MODE", "PAPER")

_VALID_BOT_MODES = {"PAPER", "LIVE", "PAUSED", "DISABLED"}
if BOT_MODE not in _VALID_BOT_MODES:
    import logging as _logging
    _logging.getLogger(__name__).warning(
        "Invalid PMB_BOT_MODE %r — must be one of %s; forcing DISABLED",
        BOT_MODE, sorted(_VALID_BOT_MODES),
    )
    BOT_MODE = "DISABLED"

TELEGRAM_BOT_TOKEN = os.getenv("PMB_BOT_TOKEN") or os.getenv("BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("PMB_CHAT_ID")   or os.getenv("TELEGRAM_CHAT_ID", "")

SCANNER_API_URL         = os.getenv("SCANNER_API_URL", "http://127.0.0.1:5000").rstrip("/")
SCANNER_TIMEOUT_SECONDS = float(os.getenv("SCANNER_TIMEOUT_SECONDS", "12"))

POLL_INTERVAL_SECONDS  = int(os.getenv("PMB_POLL_INTERVAL_SECONDS",  "60"))
MAX_SIGNAL_AGE_SECONDS = int(os.getenv("PMB_MAX_SIGNAL_AGE_SECONDS", "300"))
MAX_POSITIONS          = int(os.getenv("PMB_MAX_POSITIONS",          "5"))
MIN_SIGNAL_SCORE       = float(os.getenv("PMB_MIN_SIGNAL_SCORE",     "70"))

BASE_BUY              = float(os.getenv("PMB_BASE_BUY",    "1000"))
DIP_BUY               = float(os.getenv("PMB_DIP_BUY",    "100"))
PARTIAL_SELL          = float(os.getenv("PMB_PARTIAL_SELL","120"))
MAX_DIPS              = int(os.getenv("PMB_MAX_DIPS",       "4"))
DIP_THRESHOLD_PCT     = float(os.getenv("PMB_DIP_THRESHOLD_PCT",        "5.0"))
PARTIAL_SELL_TRIGGER_PCT = float(os.getenv("PMB_PARTIAL_SELL_TRIGGER_PCT","4.0"))
STOP_LOSS_PCT         = float(os.getenv("PMB_STOP_LOSS_PCT","20.0"))

INITIAL_CASH_BALANCE = float(os.getenv("PMB_INITIAL_CASH_BALANCE", "100000"))

BASE_DIR  = Path(__file__).resolve().parent
DATA_DIR  = Path(os.getenv("PMB_DATA_DIR", str(BASE_DIR / "data")))

POSITIONS_FILE  = DATA_DIR / "positions.json"
TRADES_FILE     = DATA_DIR / "trades.json"
STATS_FILE      = DATA_DIR / "stats.json"
