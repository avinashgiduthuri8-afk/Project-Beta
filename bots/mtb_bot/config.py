"""
PROJECT-ALPHA MTB Bot configuration.

MTB = MACD Trend Bounce.
Strategy: EMA trend confirmation + MACD momentum + Scanner confidence →
          fixed BUY → Take Profit exit (full position close).
"""

from __future__ import annotations

import os
from pathlib import Path

BOT_NAME    = "MTB"
BOT_VERSION = "2.0"
BOT_MODE    = os.getenv("MTB_BOT_MODE", "PAPER")

_VALID_BOT_MODES = {"PAPER", "LIVE", "PAUSED", "DISABLED"}
if BOT_MODE not in _VALID_BOT_MODES:
    import logging as _logging
    _logging.getLogger(__name__).warning(
        "Invalid MTB_BOT_MODE %r — must be one of %s; forcing DISABLED",
        BOT_MODE, sorted(_VALID_BOT_MODES),
    )
    BOT_MODE = "DISABLED"

TELEGRAM_BOT_TOKEN = os.getenv("MTB_BOT_TOKEN") or os.getenv("BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("MTB_CHAT_ID")   or os.getenv("TELEGRAM_CHAT_ID", "")

SCANNER_API_URL         = os.getenv("SCANNER_API_URL", "http://127.0.0.1:5000").rstrip("/")
SCANNER_TIMEOUT_SECONDS = float(os.getenv("SCANNER_TIMEOUT_SECONDS", "12"))

POLL_INTERVAL_SECONDS  = int(os.getenv("MTB_POLL_INTERVAL_SECONDS",  "60"))
MAX_SIGNAL_AGE_SECONDS = int(os.getenv("MTB_MAX_SIGNAL_AGE_SECONDS", "300"))
MAX_POSITIONS          = int(os.getenv("MTB_MAX_POSITIONS",          "3"))

TRADE_AMOUNT     = float(os.getenv("TRADE_AMOUNT", os.getenv("MTB_TRADE_AMOUNT", "110")))
INITIAL_CASH_BALANCE = float(os.getenv("MTB_INITIAL_CASH_BALANCE", "100000"))

TAKE_PROFIT_PCT = float(os.getenv("MTB_TAKE_PROFIT_PCT", "5"))
STOP_LOSS_PCT   = float(os.getenv("MTB_STOP_LOSS_PCT",   "3"))

# ── EMA / MACD / Momentum confirmation thresholds ───────────────────────────
# The scanner already computes EMA(9/21), MACD, and momentum internally.
# These thresholds gate entry on the aggregated scanner output.
MIN_SIGNAL_SCORE   = float(os.getenv("MTB_MIN_SIGNAL_SCORE",   "75"))
MIN_CONFIDENCE     = float(os.getenv("MTB_MIN_CONFIDENCE",      "50"))

# Market states that block new entries (bearish/extreme conditions)
BLOCKED_MARKET_STATES = frozenset(
    s.strip().lower()
    for s in os.getenv("MTB_BLOCKED_MARKET_STATES", "downtrend,extreme_fear,crash").split(",")
    if s.strip()
)

# EMA trend label: only enter when scanner-detected trend is in this set
# Scanner emits: breakout, bull_trend, pullback, recovery, downtrend, sideways
# Default: allow breakout, bull_trend, recovery, sideways — block downtrend via BLOCKED_MARKET_STATES
ALLOWED_MARKET_STATES = frozenset(
    s.strip().lower()
    for s in os.getenv(
        "MTB_ALLOWED_MARKET_STATES",
        "breakout,bull_trend,recovery,sideways,uptrend,neutral,unknown"
    ).split(",")
    if s.strip()
)

BASE_DIR        = Path(__file__).resolve().parent
DATA_DIR        = Path(os.getenv("MTB_DATA_DIR", str(BASE_DIR / "data")))

POSITIONS_FILE  = DATA_DIR / "positions.json"
TRADES_FILE     = DATA_DIR / "trades.json"
STATS_FILE      = DATA_DIR / "stats.json"
