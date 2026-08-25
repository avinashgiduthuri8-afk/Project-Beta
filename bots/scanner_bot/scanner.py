"""
Scanner v1 logic — extracted verbatim from CryptoScanner_MTB notebook.
Only change: added `exch_perf_90d: Optional[float] = None` to Signal dataclass
to fix the AttributeError that caused historical_filter to crash and signals
to always return [].
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import requests
import pandas as _pd
import ta as _ta


# =============================================================================
# RATE LIMITER — 8 req/s hard cap to prevent CoinDCX/Binance API bans
# Token-bucket: enforces a minimum gap between outbound HTTP calls.
# =============================================================================

_RATE_LOCK        = threading.Lock()
_RATE_LAST_CALL   = [0.0]          # mutable container so inner fn can write
_RATE_MIN_GAP_S   = 1.0 / 8        # 8 req/s → 125 ms between calls


def _limited_get(url: str, **kwargs):
    """Drop-in for requests.get() with built-in rate limiting (8 req/s max)."""
    with _RATE_LOCK:
        now = time.monotonic()
        wait = _RATE_MIN_GAP_S - (now - _RATE_LAST_CALL[0])
        if wait > 0:
            time.sleep(wait)
        _RATE_LAST_CALL[0] = time.monotonic()
    resp = requests.get(url, **kwargs)
    try:
        from . import monitoring_metrics as _mm
        _mm.record_api_call(success=resp.ok, status_code=resp.status_code)
    except Exception:
        pass
    return resp


# =============================================================================
# CONFIGURATION
# =============================================================================

try:
    BASE_DIR = Path(__file__).resolve().parent
except NameError:
    BASE_DIR = Path.cwd()

STORAGE_DIR = BASE_DIR / "data"
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_WATCHLIST = ["BTC", "SOL", "ETH", "ZEC", "XRP", "BNB"]

COINDCX_TICKER_URL  = "https://api.coindcx.com/exchange/ticker"
COINDCX_CANDLES_URL = os.getenv(
    "COINDCX_CANDLES_URL",
    "https://public.coindcx.com/market_data/candles"
)
REQUEST_TIMEOUT_SECONDS = 10
TICKER_CACHE_TTL_SECONDS = int(os.getenv("TICKER_CACHE_TTL_SECONDS", "20"))

WATCHLIST_FILE   = os.getenv("WATCHLIST_FILE",   str(STORAGE_DIR / "watchlist.json"))
SIGNAL_LOG_FILE  = os.getenv("SIGNAL_LOG_FILE",  str(STORAGE_DIR / "signals.json"))
STATS_FILE       = os.getenv("STATS_FILE",        str(STORAGE_DIR / "stats.json"))
SCANNER_LOG_FILE = os.getenv("SCANNER_LOG_FILE",  str(STORAGE_DIR / "scanner.log"))
MARKET_STATE_FILE     = os.getenv("MARKET_STATE_FILE",     str(STORAGE_DIR / "market_state.json"))
SIGNAL_STATS_FILE     = os.getenv("SIGNAL_STATS_FILE",     str(STORAGE_DIR / "signal_stats.json"))
EVALUATED_SIGNALS_FILE = os.getenv("EVALUATED_SIGNALS_FILE", str(STORAGE_DIR / "evaluated_signals.json"))
SIGNAL_HISTORY_FILE = os.getenv("SIGNAL_HISTORY_FILE", str(STORAGE_DIR / "signal_history.json"))
COIN_PERFORMANCE_FILE = os.getenv("COIN_PERFORMANCE_FILE", str(STORAGE_DIR / "coin_performance.json"))
TIER_ACCURACY_FILE = os.getenv("TIER_ACCURACY_FILE", str(STORAGE_DIR / "tier_accuracy.json"))
LIVE_SIGNALS_FILE = os.getenv("LIVE_SIGNALS_FILE", str(STORAGE_DIR / "live_signals.json"))
SETTINGS_FILE = os.getenv("SETTINGS_FILE", str(STORAGE_DIR / "settings.json"))

QUOTE_PRIORITY = ("INR", "USDT")

SCAN_INTERVAL_SECONDS      = int(os.getenv("SCAN_INTERVAL_SECONDS",      "300"))
DISCOVERY_INTERVAL_SECONDS = int(os.getenv("DISCOVERY_INTERVAL_SECONDS", "900"))
DISCOVERY_MAX_COINS        = int(os.getenv("DISCOVERY_MAX_COINS",        "500"))
SCAN_CONCURRENCY           = int(os.getenv("SCAN_CONCURRENCY",           "50"))
BOOTSTRAP_CONCURRENCY      = int(os.getenv("BOOTSTRAP_CONCURRENCY",      "30"))
BOOTSTRAP_ENABLED          = os.getenv("BOOTSTRAP_ENABLED", "true").lower() != "false"
MIN_VOLUME_24H   = float(os.getenv("MIN_VOLUME_24H",   "500000"))
MIN_LIQUIDITY_24H = float(os.getenv("MIN_LIQUIDITY_24H", "1000000"))
MIN_PRICE        = float(os.getenv("MIN_PRICE",        "0.01"))
MAX_RESULTS      = int(os.getenv("MAX_RESULTS",        "10"))
MAX_SIGNALS      = int(os.getenv("MAX_SIGNALS",        "5000"))
ALERT_COOLDOWN_SECONDS = int(os.getenv("ALERT_COOLDOWN_SECONDS", "1800"))

MODEL_VERSION = "v12.2"

# Per-file write locks.
# _write_json_lock is RLock so that _run_cleanup() in main.py can hold it
# across the full read→filter→write transaction while write_json_safely()
# re-acquires it on the same thread (NF-10: lost-update race fix).
_write_json_lock    = threading.RLock()
_scanner_state_lock = threading.Lock()
_history_lock       = threading.RLock()   # RLock: append_signal_history() holds
                                           # this while calling _write_history(),
                                           # which re-acquires on the same thread.
_coin_perf_lock     = threading.Lock()
_tier_acc_lock      = threading.Lock()

COIN_CLASSES: dict[str, set] = {
    "A": {"BTC", "ETH", "BNB", "SOL", "XRP"},
    "B": {"LINK", "AVAX", "SUI", "APT", "ARB", "NEAR", "INJ", "RENDER"},
}


def get_coin_class(symbol: str) -> str:
    sym = symbol.upper().split("-")[-1].split("_")[0]
    if sym in COIN_CLASSES["A"]:
        return "A"
    if sym in COIN_CLASSES["B"]:
        return "B"
    return "C"


def _check_candles_connectivity() -> bool:
    """Quick probe to COINDCX_CANDLES_URL before bootstrap.

    Returns True if the endpoint is reachable — any HTTP response counts as
    reachable because a server-side validation error (4xx) still proves the
    network path is open.  Only network-level exceptions (DNS failure,
    connection refused, timeout) mean the host is unreachable.

    Param fixes vs original:
    - "resolution" → "interval"  (API rejects unknown param with 422)
    - "1D"         → "1d"        (API only accepts lowercase: 1m 15m 1h 1d)
    """
    try:
        import time as _t
        now = int(_t.time())
        params = {
            "pair":     "B-BTC_USDT",
            "interval": "1d",
            "from":     now - 86400 * 3,
            "to":       now,
        }
        resp = _limited_get(
            COINDCX_CANDLES_URL,
            params=params,
            timeout=5,
        )
        logger.info(
            "[Bootstrap] Candle connectivity probe: status=%d url=%s params=%s",
            resp.status_code, COINDCX_CANDLES_URL, params,
        )
        # Any HTTP response means the network path is open.
        # 200 = data returned; 4xx = server rejected params but IS reachable.
        return True
    except Exception as exc:
        logger.warning(
            "[Bootstrap] Candle connectivity probe FAILED (network error): %s: %s",
            type(exc).__name__, exc,
        )
        return False


EMA_FAST_PERIOD     = 9
EMA_SLOW_PERIOD     = 21
PRICE_HISTORY_LIMIT = 120

# BUG-23: MTF window env vars are read and validated here.
# A value of 0 causes prices[-0:] to return the full list (not an empty slice),
# making all three timeframes identical and producing silent incorrect behaviour.
# A value of 1 makes _frame_bullish's len(slice_prices) < 2 guard always fire.
# Minimum valid window is 2. Values below their defaults are warned at startup.
_MTF_5M_WINDOW_MIN  = 2
_MTF_15M_WINDOW_MIN = 2
_MTF_1H_WINDOW_MIN  = 2

def _validated_mtf_window(env_var: str, default: int, minimum: int) -> int:
    raw = int(os.getenv(env_var, str(default)))
    if raw < minimum:
        logger.warning(
            "[Config] %s=%d is below minimum %d — clamping to %d",
            env_var, raw, minimum, minimum,
        )
        return minimum
    if raw < default:
        logger.warning(
            "[Config] %s=%d is below recommended default %d",
            env_var, raw, default,
        )
    return raw

MTF_5M_WINDOW  = _validated_mtf_window("MTF_5M_WINDOW",  10, _MTF_5M_WINDOW_MIN)
MTF_15M_WINDOW = _validated_mtf_window("MTF_15M_WINDOW", 24, _MTF_15M_WINDOW_MIN)
MTF_1H_WINDOW  = _validated_mtf_window("MTF_1H_WINDOW",  48, _MTF_1H_WINDOW_MIN)

MOMENTUM_THRESHOLD_PERCENT = 3.0
VOLUME_SPIKE_MULTIPLIER    = 2.0
VOLUME_AVERAGE_PERIOD      = 20
VOLATILITY_LOOKBACK        = 20
VOLATILITY_SPIKE_MULTIPLIER = 1.8
# BUG-19: minimum history needed for a reliable volatility baseline.
# baseline window = prices[-(VOLATILITY_LOOKBACK*2+1):-VOLATILITY_LOOKBACK]
# requires at least VOLATILITY_LOOKBACK*2+1 ticks to be non-degenerate.
VOLATILITY_MIN_HISTORY = VOLATILITY_LOOKBACK * 2 + 1  # 41

EVALUATION_HORIZONS = {
    "1h":  60 * 60,
    "4h":  4 * 60 * 60,
    "24h": 24 * 60 * 60,
    "3d":  3 * 24 * 60 * 60,
    "7d":  7 * 24 * 60 * 60,
}

# =============================================================================
# LOGGING
# =============================================================================

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("scanner_bot")
_log_handler = logging.FileHandler(SCANNER_LOG_FILE, encoding="utf-8")
_log_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
logger.addHandler(_log_handler)


# =============================================================================
# STORAGE HELPERS
# =============================================================================

# OPT-4: Minimum gap between backups of the same file (5 minutes).
# backup_file() is called on every write_json_safely(); without throttling
# every scan-cycle disk write triggers an extra copy operation.
_BACKUP_MIN_INTERVAL = 300.0   # seconds
_backup_last_at: dict = {}     # path-str → monotonic timestamp of last backup


def backup_file(path: Path) -> None:
    if not path.exists():
        return
    now = time.monotonic()
    key = str(path)
    if now - _backup_last_at.get(key, 0.0) < _BACKUP_MIN_INTERVAL:
        return  # throttled — too soon since last backup
    backup_path = path.with_suffix(path.suffix + ".bak")
    try:
        shutil.copy2(path, backup_path)
        _backup_last_at[key] = now
    except OSError:
        pass


def write_json_safely(path: Path, data) -> None:
    with _write_json_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        backup_file(path)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temp_path.replace(path)


def ensure_storage_files() -> None:
    """
    I-12: Create missing files with safe defaults — NEVER overwrite existing data.
    For each critical file: if missing, try to restore from hourly backup first;
    only write a blank default if no backup exists either.
    """
    BACKUP_DIR_PATH = STORAGE_DIR / "backups"

    _FILES: dict[str, tuple[str, object]] = {
        "watchlist.json":        ("watchlist_backup.json",        {"coins": DEFAULT_WATCHLIST}),
        "signals.json":          ("signals_backup.json",          {"signals": []}),
        "stats.json":            ("stats_backup.json",            {}),
        "live_signals.json":     ("live_signals_backup.json",     {"signals": []}),
        "signal_history.json":   ("signal_history_backup.json",   {"signals": []}),
        "coin_performance.json": ("coin_performance_backup.json", {}),
        "tier_accuracy.json":    ("tier_accuracy_backup.json",    {}),
        "settings.json":         ("settings_backup.json",         {}),
    }

    for name, (backup_name, default) in _FILES.items():
        path = STORAGE_DIR / name
        if path.exists():
            continue  # file present — never touch it
        # Try to restore from backup (hourly backup or shutdown save)
        backup_path = BACKUP_DIR_PATH / backup_name
        if backup_path.exists():
            try:
                import shutil as _shutil
                _shutil.copy2(str(backup_path), str(path))
                logger.info("I-12: Restored %s from backup %s", name, backup_name)
                continue
            except OSError:
                logger.warning("I-12: Could not restore %s from backup — using empty default", name, exc_info=True)
        # No backup available — write a blank default so the file exists
        write_json_safely(path, default)

    (STORAGE_DIR / "scanner.log").touch(exist_ok=True)
    logger.info("Storage ready: %s", STORAGE_DIR)


ensure_storage_files()


# =============================================================================
# WATCHLIST STORAGE
# =============================================================================

# BUG-25/26/30: centralized coin-symbol validation, shared by the watchlist
# API endpoint (main.py) and anything else that needs to validate a coin
# symbol before it reaches WatchlistStore.add(). Single source of truth so
# validation rules cannot drift or be duplicated across call sites.
COIN_SYMBOL_MAX_LENGTH = 10
_COIN_SYMBOL_RE = re.compile(r"^[A-Z0-9]+$")


def validate_coin_symbol(raw: str) -> tuple[bool, str, str]:
    """
    Normalize and validate a coin symbol.

    Returns (is_valid, normalized_symbol, reason).
    - is_valid=True  → normalized_symbol is safe to store; reason=""
    - is_valid=False → normalized_symbol is the best-effort normalized form
      (for error reporting); reason explains why it was rejected.

    Rules:
    - Trimmed and uppercased before validation.
    - Must be non-empty after trimming (BUG-25).
    - Must contain only A-Z and 0-9 (BUG-26/30) — rejects '/', '-', ';',
      '$', '@', internal spaces, and any other punctuation.
    - Must be at most COIN_SYMBOL_MAX_LENGTH characters (BUG-26).
    """
    normalized = raw.strip().upper()
    if not normalized:
        return False, "", "invalid_coin"
    if len(normalized) > COIN_SYMBOL_MAX_LENGTH:
        return False, normalized, "invalid_coin"
    if not _COIN_SYMBOL_RE.match(normalized):
        return False, normalized, "invalid_coin"
    return True, normalized, ""


class WatchlistStore:
    # OPT-2: 30-second TTL avoids a disk read on every scan-cycle call to all().
    # The I-11 guarantee (seeing external writes) still holds within one TTL window.
    # add() and remove() always force-expire the cache so callers see changes immediately.
    _CACHE_TTL = 30.0  # seconds

    def __init__(self, path: str = WATCHLIST_FILE):
        self.path = Path(path)
        self._coins = self._load()
        self._pair_map: dict = self._load_pair_map()
        self._cache_time: float = 0.0
        self._cache_value: list = list(self._coins)

    def _load(self) -> list[str]:
        if not self.path.exists():
            return list(DEFAULT_WATCHLIST)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("Could not read watchlist file; using defaults", exc_info=True)
            return list(DEFAULT_WATCHLIST)
        coins = data.get("coins", data if isinstance(data, list) else [])
        normalized = []
        for coin in coins:
            c = str(coin).upper().strip()
            if not c:
                continue
            for suffix in ("USDT", "BUSD", "INR", "BTC"):
                if c.endswith(suffix) and len(c) > len(suffix):
                    c = c[: -len(suffix)]
                    break
            normalized.append(c)
        return list(dict.fromkeys(normalized)) or list(DEFAULT_WATCHLIST)

    def _load_pair_map(self) -> dict:
        """Load stored pair metadata. Returns {} for backward compat with old files."""
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return dict(data.get("pair_map", {}))
        except (OSError, json.JSONDecodeError):
            return {}

    def save(self) -> None:
        write_json_safely(self.path, {"coins": self._coins, "pair_map": self._pair_map})

    def all(self) -> list[str]:
        # I-11: reload from disk so scanner sees writes from watchlist_manager.py.
        # OPT-2: throttled to once per _CACHE_TTL to avoid per-scan disk reads.
        now = time.monotonic()
        if now - self._cache_time < self._CACHE_TTL:
            try:
                from . import monitoring_metrics as _mm
                _mm.record_cache("watchlist", hit=True)
            except Exception:
                pass
            return list(self._cache_value)
        try:
            from . import monitoring_metrics as _mm
            _mm.record_cache("watchlist", hit=False)
        except Exception:
            pass
        self._coins = self._load()
        self._pair_map = self._load_pair_map()
        self._cache_value = list(self._coins)
        self._cache_time = now
        return list(self._cache_value)

    def add(self, coin: str) -> bool:
        normalized = coin.upper().strip()
        # T5.4: hold _write_json_lock for the full check→mutate→save sequence.
        # _write_json_lock is RLock so save()→write_json_safely() can re-acquire
        # it on this same thread.  This is the ONE canonical lock for watchlist.json;
        # no second lock is introduced.
        with _write_json_lock:
            if not normalized or normalized in self._coins:
                return False
            self._coins.append(normalized)
            self.save()
            self._cache_time = 0.0  # expire cache inside lock so all() sees fresh state
        return True

    def remove(self, coin: str) -> bool:
        normalized = coin.upper().strip()
        # T5.4: same atomic read-modify-write guarantee as add().
        with _write_json_lock:
            if normalized not in self._coins:
                return False
            self._coins.remove(normalized)
            self._pair_map.pop(normalized, None)  # clean up pair metadata
            self.save()
            self._cache_time = 0.0  # expire cache inside lock so all() sees fresh state
        return True

    # ── Pair resolution storage ─────────────────────────────────────────────

    def set_pair(self, coin: str, pair: str, quote: str) -> None:
        """Store resolved pair metadata for *coin*. Persists immediately."""
        # T5.4: atomic under the same canonical lock as add/remove.
        with _write_json_lock:
            self._pair_map[coin.upper()] = {"pair": pair, "quote": quote}
            self.save()

    def get_pair(self, coin: str) -> dict | None:
        """Return stored pair metadata or None if not yet resolved."""
        return self._pair_map.get(coin.upper())

    def all_with_pairs(self) -> list[dict]:
        """Return the watchlist as [{coin, pair, quote}].

        Coins without stored pair metadata have pair=None, quote=None.
        Callers should run lazy resolution and call set_pair() to persist.
        """
        coins = self.all()
        return [
            {
                "coin":  c,
                "pair":  self._pair_map.get(c, {}).get("pair"),
                "quote": self._pair_map.get(c, {}).get("quote"),
            }
            for c in coins
        ]


# =============================================================================
# SIGNAL DATACLASS
# =============================================================================

@dataclass(frozen=True)
class Signal:
    coin: str
    kind: str
    score: int
    message: str
    price: float
    volume: float
    created_at: datetime
    tier: str
    reasons: list
    volume_strength: float
    momentum_strength: float
    model_version: str = MODEL_VERSION
    phase5_trend: int = 0
    phase5_pullback: int = 0
    phase5_momentum: int = 0
    phase5_risk_reward: int = 0
    phase5_total: int = 0
    final_score: int = 0
    hist_trend_7d:   int = 0
    hist_trend_30d:  int = 0
    hist_trend_90d:  int = 0
    hist_sr_quality: int = 0
    hist_vol_score:  int = 0
    hist_total:      int = 0
    coin_class:      str = "C"
    market_state:    str = ""
    opportunity_type: str = ""
    opp_confidence:   int = 0
    opportunity_score: int = 0
    priority:          str = ""
    risk_level:        str = ""
    # I-10: INR Market Support — track which market the signal was generated from
    market:            str = "INR"
    # BUG FIX: this field was missing, causing AttributeError in historical_filter
    exch_perf_90d: Optional[float] = None


# =============================================================================
# MATH HELPERS
# =============================================================================

def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def ema(values: list, period: int) -> list:
    if not values:
        return []
    multiplier = 2 / (period + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append((value - result[-1]) * multiplier + result[-1])
    return result


def percent_change(old: float, new: float) -> float:
    if old == 0:
        return 0.0
    return ((new - old) / old) * 100


def average(values: list) -> float:
    return sum(values) / len(values) if values else 0.0


def volatility(prices: list) -> float:
    moves = [abs(percent_change(prices[i - 1], prices[i])) for i in range(1, len(prices))]
    return average(moves)


def trend_summary(history: list) -> dict:
    # BUG-24: the original gate (< 2) allowed EMA(9) and EMA(21) to run on as
    # few as 2 ticks. Both EMAs seed from prices[0] and the faster multiplier
    # always diverges upward first on any rising pair, producing a spurious
    # 'uptrend' result. Use ANALYZE_MIN_HISTORY (EMA_SLOW_PERIOD + 1 = 22) so
    # the EMA has enough data to be meaningful before reporting a direction.
    # Return 'neutral' (not 'warming up') to signal insufficient history clearly.
    if len(history) < ANALYZE_MIN_HISTORY:
        return {"trend": "neutral", "move_percent": 0.0}
    prices = [item["price"] for item in history]
    lookback = min(10, len(prices) - 1)
    move = percent_change(prices[-lookback - 1], prices[-1])
    fast = ema(prices, EMA_FAST_PERIOD)[-1]
    slow = ema(prices, EMA_SLOW_PERIOD)[-1]
    if fast > slow and move > 0:
        trend = "uptrend"
    elif fast < slow and move < 0:
        trend = "downtrend"
    else:
        trend = "sideways"
    return {"trend": trend, "move_percent": move, "ema_fast": fast, "ema_slow": slow}


# =============================================================================
# HISTORICAL CANDLES / PATTERN SCORE
# =============================================================================

_candle_cache: dict[str, tuple[float, list]] = {}
_candle_cache_lock = threading.Lock()
CANDLE_CACHE_TTL = 3600


def _fetch_daily_candles(market_pair: str, days: int = 95) -> list:
    import time as _time
    now = _time.time()
    with _candle_cache_lock:
        cached = _candle_cache.get(market_pair)
        if cached and now - cached[0] < CANDLE_CACHE_TTL:
            try:
                from . import monitoring_metrics as _mm
                _mm.record_cache("candle", hit=True)
            except Exception:
                pass
            return cached[1]
    try:
        from . import monitoring_metrics as _mm
        _mm.record_cache("candle", hit=False)
    except Exception:
        pass
    to_ts   = int(now)
    from_ts = to_ts - days * 86400
    try:
        resp = _limited_get(
            COINDCX_CANDLES_URL,
            # "resolution" → "interval", "1D" → "1d"  (API rejects both wrong forms with 422)
            params={"pair": market_pair, "interval": "1d", "from": from_ts, "to": to_ts},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and data:
            with _candle_cache_lock:
                _candle_cache[market_pair] = (now, data)
            return data
    except Exception:
        logger.debug("Candle fetch failed for %s", market_pair, exc_info=True)
    return []


def _coin_to_pair(coin: str) -> list:
    coin = coin.upper()
    return [f"B-{coin}_INR", f"B-{coin}_USDT", f"B-{coin}_BTC"]


def resolve_coin_pair(coin: str, tickers: list | None = None) -> dict:
    """
    Resolve a coin symbol to its best available trading pair.

    Priority (matches QUOTE_PRIORITY): INR > USDT > rejected.

    When *tickers* is provided (the live ticker list from the scanner cache),
    the function checks which pairs actually exist in the feed and returns the
    highest-priority one.  When *tickers* is None (cache not yet warm), it
    falls back to returning the INR pair as the best-guess default without
    making any additional API calls.

    This is a **pure function** — no I/O, no network calls, no side-effects.

    Parameters
    ----------
    coin    : str  — base coin symbol, e.g. "BTC", "PEPE"
    tickers : list | None — raw ticker list from the scanner (optional)

    Returns
    -------
    On success::

        {
            "coin":     "BTC",
            "pair":     "B-BTC_INR",
            "quote":    "INR",
            "resolved": True,
        }

    On failure (pair not found in tickers)::

        {
            "coin":     "BADCOIN",
            "pair":     None,
            "quote":    None,
            "resolved": False,
            "reason":   "no_pair_found",
        }
    """
    coin = coin.upper().strip()

    if tickers:
        available = {str(t.get("market", "")).upper() for t in tickers}
        for quote in QUOTE_PRIORITY:
            pair = f"B-{coin}_{quote}"
            if pair in available:
                return {"coin": coin, "pair": pair, "quote": quote, "resolved": True}
        return {
            "coin": coin,
            "pair": None,
            "quote": None,
            "resolved": False,
            "reason": "no_pair_found",
        }

    pair = f"B-{coin}_INR"
    return {"coin": coin, "pair": pair, "quote": "INR", "resolved": True, "reason": "no_cache"}


def _fetch_coin_closes(coin: str) -> list:
    """OPT-1: Fetch and parse daily candle closes for a coin in one place.

    Tries each pair in QUOTE_PRIORITY order (INR → USDT → BTC) and returns
    the closes from the **first pair that has a non-empty candle response**,
    applying the BUG-17 zero/negative close filter.  Returns [] when no pair
    has any candle data.

    Pair-selection semantics deliberately match historical_pattern_score():
    "first pair with any candle payload wins" — the count check is left to each
    consumer (≥5 for hist score, ≥2 for perf data).  This ensures shared-closes
    produce identical scores to the pre-OPT-1 per-function fetches.

    Call once per analyze_coin() invocation; pass the result to both
    historical_pattern_score() and get_historical_performance() to eliminate
    the duplicate candle API call that previously occurred for every coin that
    passed the EMA + volume gates.
    """
    for pair in _coin_to_pair(coin):
        candles = _fetch_daily_candles(pair, days=95)
        if not candles:
            continue
        candles_sorted = sorted(candles, key=lambda c: c.get("time", 0))
        closes = [
            v for c in candles_sorted
            if (v := float(c.get("close", c.get("c", 0)) or 0)) > 0
        ]
        # Return on first non-empty candle response regardless of close count.
        # Each consumer enforces its own minimum: historical_pattern_score(≥5),
        # get_historical_performance(≥2).
        return closes
    return []


def _trend_score_from_closes(closes: list, max_pts: int = 25) -> int:
    if len(closes) < 2:
        return 0
    net_ret    = percent_change(closes[0], closes[-1])
    ret_score  = _clamp(net_ret / 60.0, -1.0, 1.0)
    up_days    = sum(1 for i in range(1, len(closes)) if closes[i] > closes[i - 1])
    consistency = up_days / (len(closes) - 1)
    raw = (ret_score * 0.6 + (consistency * 2 - 1) * 0.4)
    normalised = (raw + 1) / 2
    return int(round(_clamp(normalised * max_pts, 0, max_pts)))


def _sr_quality_score(closes: list, current_price: float, max_pts: int = 25) -> int:
    if len(closes) < 10 or current_price <= 0:
        return 12
    band = 0.015
    levels: list = []
    for ref in closes:
        if ref <= 0:          # BUG-17: skip zero/negative closes — would cause ZeroDivisionError
            continue
        touches = sum(1 for c in closes if abs(c - ref) / ref <= band)
        if touches >= 3 and not any(abs(ref - lv) / ref <= band for lv in levels):
            levels.append(ref)
    if not levels:
        return 12
    supports    = [lv for lv in levels if lv <= current_price * 1.02]
    resistances = [lv for lv in levels if lv >  current_price * 1.02]
    support_proximity  = 0.0
    resistance_context = 0.0
    if supports:
        nearest_sup = max(supports)
        dist = abs(current_price - nearest_sup) / current_price
        support_proximity = _clamp(1.0 - dist / 0.05, 0.0, 1.0)
    if resistances:
        nearest_res = min(resistances)
        dist = (nearest_res - current_price) / current_price
        resistance_context = _clamp(1.0 - dist / 0.10, 0.0, 1.0) * 0.5
    raw = support_proximity * 0.7 + resistance_context
    return int(round(_clamp(raw * max_pts, 0, max_pts)))


def _hist_vol_score(closes: list, max_pts: int = 25) -> int:
    if len(closes) < 5:
        return 12
    avg_daily_move = average([abs(percent_change(closes[i-1], closes[i])) for i in range(1, len(closes))])
    ideal = 1.5
    deviation = abs(avg_daily_move - ideal) / ideal
    raw = _clamp(1.0 - deviation * 0.6, 0.0, 1.0)
    return int(round(_clamp(raw * max_pts, 0, max_pts)))


@dataclass(frozen=True)
class HistoricalPatternScore:
    trend_7d:   int
    trend_30d:  int
    trend_90d:  int
    sr_quality: int
    hist_vol:   int
    total:      int


def historical_pattern_score(coin: str, current_price: float, closes: Optional[list] = None) -> HistoricalPatternScore:
    # BUG-20: wrap the entire computation in a try/except so that any unexpected
    # exception (e.g. malformed candle data, future API changes) cannot propagate
    # into analyze_coin → _scan_ticker → _scan_many. Log clearly and return neutral.
    _NEUTRAL = HistoricalPatternScore(12, 12, 12, 12, 12, 60)
    try:
        if closes is None:
            # OPT-1 fallback: fetch closes when not pre-supplied by analyze_coin()
            candles: list = []
            for pair in _coin_to_pair(coin):
                candles = _fetch_daily_candles(pair, days=95)
                if candles:
                    break
            if not candles:
                return _NEUTRAL
            candles = sorted(candles, key=lambda c: c.get("time", 0))
            # BUG-17: explicitly exclude <= 0 after float conversion — string '0.0' is truthy
            # but becomes 0.0 after float(), which causes ZeroDivisionError in _sr_quality_score
            closes  = [
                v for c in candles
                if (v := float(c.get("close", c.get("c", 0)) or 0)) > 0
            ]
        if len(closes) < 5:
            return _NEUTRAL
        trend_7d   = _trend_score_from_closes(closes[-7:]  if len(closes) >= 7  else closes)
        trend_30d  = _trend_score_from_closes(closes[-30:] if len(closes) >= 30 else closes)
        trend_90d  = _trend_score_from_closes(closes[-90:] if len(closes) >= 90 else closes)
        sr_quality = _sr_quality_score(closes, current_price)
        hist_vol   = _hist_vol_score(closes[-90:] if len(closes) >= 90 else closes)
        t7  = int(_clamp(trend_7d,   0, 25))
        t30 = int(_clamp(trend_30d,  0, 25))
        t90 = int(_clamp(trend_90d,  0, 25))
        sr  = int(_clamp(sr_quality, 0, 25))
        hv  = int(_clamp(hist_vol,   0, 25))

        # RSI-14 momentum quality score (ta library) — 0 to 20 bonus pts
        rsi_score = 10  # neutral default
        try:
            if len(closes) >= 15:
                _closes_s = _pd.Series(closes, dtype=float)
                _rsi_val  = float(_ta.momentum.rsi(_closes_s, window=14, fillna=True).iloc[-1])
                if 45 <= _rsi_val <= 65:
                    rsi_score = 20   # ideal bullish momentum
                elif 35 <= _rsi_val < 45 or 65 < _rsi_val <= 72:
                    rsi_score = 14   # recovering or mild overbought
                elif _rsi_val < 35:
                    rsi_score = 8    # oversold — caution but may bounce
                else:
                    rsi_score = 4    # overbought (>72) — high reversal risk
        except Exception:
            pass  # keep neutral 10 on any ta failure

        # Bollinger Band Width score (ta library) — 0 to 15 bonus pts
        # Narrow bands = coiling before breakout (high score).
        # Very wide bands = already moved / risky entry (low score).
        bb_score = 8  # neutral default
        try:
            if len(closes) >= 21:
                _closes_s = _pd.Series(closes, dtype=float)
                _bb = _ta.volatility.BollingerBands(_closes_s, window=20, window_dev=2, fillna=True)
                _bbw = float(_bb.bollinger_wband().iloc[-1])  # bandwidth as % of SMA
                if _bbw < 5:
                    bb_score = 15   # tight squeeze — breakout coiling
                elif _bbw < 10:
                    bb_score = 12   # moderate squeeze — building momentum
                elif _bbw < 20:
                    bb_score = 8    # normal — neutral
                elif _bbw < 35:
                    bb_score = 4    # wide — momentum already in motion
                else:
                    bb_score = 1    # extremely wide — volatile/risky entry
        except Exception:
            pass  # keep neutral 8 on any ta failure

        # MACD crossover score (ta library) — 0 to 15 bonus pts
        # Fresh bullish crossover = highest score; bearish crossover = lowest.
        # Needs 35+ candles for MACD(12,26) + signal(9) to be meaningful.
        macd_score = 7  # neutral default
        try:
            if len(closes) >= 35:
                _closes_s   = _pd.Series(closes, dtype=float)
                _macd_ind   = _ta.trend.MACD(_closes_s, window_slow=26, window_fast=12,
                                              window_sign=9, fillna=True)
                _hist       = _macd_ind.macd_diff()   # histogram = MACD - Signal
                _curr_hist  = float(_hist.iloc[-1])
                _prev_hist  = float(_hist.iloc[-2])
                _macd_val   = float(_macd_ind.macd().iloc[-1])

                if _prev_hist < 0 and _curr_hist > 0:
                    macd_score = 15   # fresh bullish crossover — strongest entry signal
                elif _curr_hist > 0 and _macd_val > 0:
                    macd_score = 12   # MACD above signal & zero line — confirmed uptrend
                elif _curr_hist > 0 and _macd_val <= 0:
                    macd_score = 10   # bullish cross below zero — early recovery
                elif _prev_hist > 0 and _curr_hist < 0:
                    macd_score = 2    # fresh bearish crossover — avoid
                elif _curr_hist < 0 and _macd_val < 0:
                    macd_score = 3    # MACD below signal & zero — bearish
                else:
                    macd_score = 5    # weakening momentum above zero
        except Exception:
            pass  # keep neutral 7 on any ta failure

        raw_total = t7 + t30 + t90 + sr + hv + rsi_score + bb_score + macd_score
        total = int(_clamp(raw_total, 0, 100))
        return HistoricalPatternScore(trend_7d=t7, trend_30d=t30, trend_90d=t90, sr_quality=sr, hist_vol=hv, total=total)
    except Exception as exc:
        # BUG-20: log coin, actual exception type, and message explicitly
        # so diagnostics are visible even when traceback output is suppressed.
        logger.error(
            "[HistScore] coin=%s exception_type=%s message=%s — returning neutral score",
            coin,
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        return _NEUTRAL


def get_historical_performance(coin: str, closes: Optional[list] = None) -> dict:
    def _pct(closes, n):
        window = closes[-(n + 1):]
        if len(window) < 2:
            return None
        o = window[0]; c = window[-1]
        return round((c - o) / o * 100, 4) if o > 0 else None

    # OPT-1: use pre-fetched closes when supplied by analyze_coin().
    # Fall through to the per-pair loop only when closes is None (standalone
    # call) or has fewer than 2 valid values (rare corrupt-data edge case that
    # preserves original retry-next-pair behaviour for perf data).
    if closes is not None and len(closes) >= 2:
        return {
            "coin":     coin,
            "perf_7d":  _pct(closes, 7),
            "perf_14d": _pct(closes, 14),
            "perf_30d": _pct(closes, 30),
            "perf_90d": _pct(closes, 90),
            "source":   "pre-fetched",
            "error":    None,
        }
    if closes is not None:
        # Pre-fetched but insufficient — no candle data available for this coin
        return {"coin": coin, "perf_7d": None, "perf_14d": None, "perf_30d": None, "perf_90d": None, "source": None, "error": "no candle data available"}

    for pair in _coin_to_pair(coin):
        candles = _fetch_daily_candles(pair, days=95)
        if not candles:
            continue
        candles_sorted = sorted(candles, key=lambda c: c.get("time", 0))
        closes = [
            float(c.get("close", c.get("c", 0)) or 0)
            for c in candles_sorted
            if float(c.get("close", c.get("c", 0)) or 0) > 0
        ]
        if len(closes) < 2:
            continue
        return {
            "coin":     coin,
            "perf_7d":  _pct(closes, 7),
            "perf_14d": _pct(closes, 14),
            "perf_30d": _pct(closes, 30),
            "perf_90d": _pct(closes, 90),
            "source":   pair,
            "error":    None,
        }
    return {"coin": coin, "perf_7d": None, "perf_14d": None, "perf_30d": None, "perf_90d": None, "source": None, "error": "no candle data available"}


# =============================================================================
# BOOTSTRAP  (SP1.1 — Historical Data Bootstrap & Recovery)
# =============================================================================

BOOTSTRAP_CANDLES_URL = COINDCX_CANDLES_URL
# API accepts: 1m, 15m, 1h, 1d  ("5m" is not a valid value — was returning 422)
BOOTSTRAP_INTERVAL    = "15m"
BOOTSTRAP_LIMIT       = PRICE_HISTORY_LIMIT

_READY_EMA    = EMA_SLOW_PERIOD       # 21 — minimum ticks for EMA calculation
_READY_MTF_5M = MTF_5M_WINDOW         # 10
_READY_MTF_15 = MTF_15M_WINDOW        # 24
_READY_MTF_1H = MTF_1H_WINDOW         # 48
_READY_P5     = 20

# BUG-18: minimum history required before phase5_score() may run EMA calculations.
# EMA(21) needs at least EMA_SLOW_PERIOD ticks to produce a meaningful value.
PHASE5_MIN_HISTORY = EMA_SLOW_PERIOD  # 21

# BUG-16: minimum history required before analyze_coin() may produce signals.
# EMA(21) needs EMA_SLOW_PERIOD ticks + 1 to allow a crossover comparison
# (needs fast[-2]/slow[-2] vs fast[-1]/slow[-1]), and the volume baseline
# needs VOLUME_AVERAGE_PERIOD + 1 ticks (21). EMA_SLOW_PERIOD + 1 = 22
# satisfies both.
ANALYZE_MIN_HISTORY = EMA_SLOW_PERIOD + 1  # 22

# Minimum useful candle count: must be enough for at least EMA gate.
# Histories shorter than this are treated as failed downloads.
_BOOTSTRAP_MIN_CANDLES = _READY_EMA   # 21

# Per-coin retry policy for bootstrap candle downloads.
_BOOTSTRAP_MAX_RETRIES    = 3
_BOOTSTRAP_RETRY_DELAY_S  = 2.0       # seconds between retries


@dataclass
class BootstrapResult:
    """
    Summary produced by bootstrap_price_history().

    Fields
    ------
    coins_attempted : total coins fed into the bootstrap
    coins_loaded    : coins with history >= _BOOTSTRAP_MIN_CANDLES after bootstrap
    coins_failed    : coins that returned no usable history
    coins_skipped   : coins already had sufficient history (skipped re-download)
    avg_history_len : mean candle count across loaded coins
    min_history_len : minimum candle count across loaded coins
    ema_ready       : True if min_history_len >= _READY_EMA
    mtf_ready       : True if min_history_len >= _READY_MTF_1H
    phase5_ready    : True if min_history_len >= _READY_P5
    duration_s      : wall-clock seconds for the full bootstrap
    failed_coins    : list[str] of coins that could not be loaded
    """
    coins_attempted: int = 0
    coins_loaded:    int = 0
    coins_failed:    int = 0
    coins_skipped:   int = 0
    avg_history_len: float = 0.0
    min_history_len: int   = 0
    ema_ready:       bool = False
    mtf_ready:       bool = False
    phase5_ready:    bool = False
    duration_s:      float = 0.0
    failed_coins:    list = None   # list[str]

    def __post_init__(self):
        if self.failed_coins is None:
            self.failed_coins = []

    def summary_lines(self) -> list:
        return [
            "[Bootstrap] Startup history pre-load complete",
            f"  Coins attempted  : {self.coins_attempted}",
            f"  Loaded           : {self.coins_loaded}",
            f"  Skipped (cached) : {self.coins_skipped}",
            f"  Failed           : {self.coins_failed} ({len(self.failed_coins)} coins)",
            f"  Avg history len  : {self.avg_history_len:.1f} ticks",
            f"  Min history len  : {self.min_history_len} ticks",
            f"  Ready for EMA    : {'YES' if self.ema_ready else 'NO'} (need {_READY_EMA})",
            f"  Ready for MTF    : {'YES' if self.mtf_ready else 'NO'} (need {_READY_MTF_1H})",
            f"  Ready for Phase5 : {'YES' if self.phase5_ready else 'NO'} (need {_READY_P5})",
            f"  Duration         : {self.duration_s:.1f}s",
        ]


def _bootstrap_pair_candidates(coin: str) -> list:
    coin = coin.upper()
    return [(f"B-{coin}_INR", "INR"), (f"B-{coin}_USDT", "USDT")]


def _fetch_bootstrap_candles(coin: str) -> list:
    """
    Download bootstrap candles for *coin*, trying INR then USDT pairs.

    SP1.1 fixes applied:
    - Retries each pair up to _BOOTSTRAP_MAX_RETRIES times on network/timeout errors.
    - Logs each retry attempt with attempt number and error.
    - Accepts only data with >= _BOOTSTRAP_MIN_CANDLES entries (not just >= 2).
    - Distinguishes between empty-response (API OK, no data) and network errors.
    - Returns [] only after all pairs and all retries are exhausted.
    """
    for pair, _quote in _bootstrap_pair_candidates(coin):
        for attempt in range(1, _BOOTSTRAP_MAX_RETRIES + 1):
            try:
                resp = _limited_get(
                    BOOTSTRAP_CANDLES_URL,
                    params={
                        "pair":     pair,
                        "interval": BOOTSTRAP_INTERVAL,
                        "limit":    BOOTSTRAP_LIMIT,
                    },
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
                # Non-200: log and try next pair immediately (not a retryable error)
                if resp.status_code != 200:
                    logger.debug(
                        "Bootstrap: coin=%s pair=%s HTTP %d — skipping pair",
                        coin, pair, resp.status_code,
                    )
                    break

                data = resp.json()

                # Empty or non-list response: API is up but has no data for this pair
                if not isinstance(data, list) or len(data) == 0:
                    logger.debug(
                        "Bootstrap: coin=%s pair=%s empty response — skipping pair",
                        coin, pair,
                    )
                    break

                # Partial data: fewer candles than the minimum required for indicators
                if len(data) < _BOOTSTRAP_MIN_CANDLES:
                    logger.warning(
                        "Bootstrap: coin=%s pair=%s returned only %d candles "
                        "(need %d) — skipping pair",
                        coin, pair, len(data), _BOOTSTRAP_MIN_CANDLES,
                    )
                    break

                # Success
                logger.debug(
                    "Bootstrap: coin=%s pair=%s loaded %d candles (attempt %d/%d)",
                    coin, pair, len(data), attempt, _BOOTSTRAP_MAX_RETRIES,
                )
                return data

            except requests.exceptions.Timeout:
                logger.warning(
                    "Bootstrap: coin=%s pair=%s timeout on attempt %d/%d",
                    coin, pair, attempt, _BOOTSTRAP_MAX_RETRIES,
                )
            except requests.exceptions.ConnectionError as exc:
                logger.warning(
                    "Bootstrap: coin=%s pair=%s connection error on attempt %d/%d: %s",
                    coin, pair, attempt, _BOOTSTRAP_MAX_RETRIES, exc,
                )
            except Exception as exc:
                logger.warning(
                    "Bootstrap: coin=%s pair=%s unexpected error on attempt %d/%d: %s",
                    coin, pair, attempt, _BOOTSTRAP_MAX_RETRIES, exc,
                )

            # Delay before retry (not after the last attempt)
            if attempt < _BOOTSTRAP_MAX_RETRIES:
                time.sleep(_BOOTSTRAP_RETRY_DELAY_S)

    # All pairs and retries exhausted
    logger.warning("Bootstrap: coin=%s — all pairs failed, no history loaded", coin)
    return []


def _candles_to_history(candles: list) -> list:
    """
    Convert raw CoinDCX candle dicts to the internal price-history format.

    SP1.1 fixes applied:
    - Deduplicates by timestamp (keeps last entry per timestamp — handles API glitches
      where the same candle appears twice in a response).
    - Validates close > 0 and ts_ms > 0 before inclusion (unchanged).
    - Sorts by time and caps at PRICE_HISTORY_LIMIT (unchanged).
    """
    seen_ts: dict[int, dict] = {}   # ts_ms → entry; last write wins (dedup)
    for c in candles:
        try:
            close  = float(c.get("close",  c.get("c", 0)) or 0)
            volume = float(c.get("volume", c.get("v", 0)) or 0)
            ts_ms  = int(c.get("time",     c.get("t", 0)) or 0)
            if close <= 0 or ts_ms <= 0:
                continue
            seen_ts[ts_ms] = {
                "time":   datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc),
                "price":  close,
                "volume": volume,
            }
        except (TypeError, ValueError, KeyError):
            continue

    result = sorted(seen_ts.values(), key=lambda x: x["time"])
    return result[-PRICE_HISTORY_LIMIT:]


async def bootstrap_price_history(
    coins: list,
    price_history: dict,
    concurrency: int = BOOTSTRAP_CONCURRENCY,
) -> BootstrapResult:
    """
    Download 5-minute candle history for every coin in *coins* and populate
    *price_history* in-place.

    SP1.1 fixes applied:
    - return_exceptions=True: a single coin failure no longer aborts all others.
    - Per-coin exceptions are caught and that coin is added to failed_coins.
    - Coins already holding >= _READY_EMA ticks are skipped (counted separately).
    - Only histories with >= _BOOTSTRAP_MIN_CANDLES ticks are accepted as loaded.
    - Structured log lines emitted at each stage.
    - BootstrapResult now includes coins_skipped and min_history_len.
    """
    import time as _time

    t_start = _time.monotonic()
    sem     = asyncio.Semaphore(concurrency)
    results: dict[str, list | None] = {}    # coin → history list, or None on failure

    logger.info(
        "[Bootstrap] Starting — coins=%d concurrency=%d min_candles=%d",
        len(coins), concurrency, _BOOTSTRAP_MIN_CANDLES,
    )

    async def _fetch_one(coin: str) -> None:
        # SP1.1: coins with sufficient history are skipped (no re-download)
        existing = price_history.get(coin, [])
        if len(existing) >= _READY_EMA:
            results[coin] = existing
            return
        async with sem:
            candles = await asyncio.to_thread(_fetch_bootstrap_candles, coin)
        history = _candles_to_history(candles) if candles else None
        # SP1.1: reject partial histories that can't feed the EMA gate
        if history is not None and len(history) < _BOOTSTRAP_MIN_CANDLES:
            logger.warning(
                "[Bootstrap] coin=%s: history too short after conversion (%d ticks, need %d) — treating as failed",
                coin, len(history), _BOOTSTRAP_MIN_CANDLES,
            )
            history = None
        results[coin] = history

    # SP1.1: return_exceptions=True — one coin failing does NOT abort all others
    tasks = [_fetch_one(c) for c in coins]
    outcomes = await asyncio.gather(*tasks, return_exceptions=True)

    # Absorb any task-level exceptions (shouldn't happen since _fetch_one is
    # guarded, but belt-and-suspenders)
    for coin, outcome in zip(coins, outcomes):
        if isinstance(outcome, Exception):
            logger.error(
                "[Bootstrap] coin=%s: unexpected task exception: %s",
                coin, outcome, exc_info=outcome,
            )
            results.setdefault(coin, None)

    # Tally results
    loaded       = 0
    failed_coins: list[str] = []
    skipped      = 0
    hist_lens:   list[int]  = []

    for coin, history in results.items():
        existing_len = len(price_history.get(coin, []))
        if history is not None and history is price_history.get(coin):
            # Was a skip (already had enough history)
            skipped += 1
            hist_lens.append(existing_len)
        elif history:
            price_history[coin] = history
            loaded += 1
            hist_lens.append(len(history))
            logger.debug("[Bootstrap] coin=%s loaded %d ticks", coin, len(history))
        else:
            failed_coins.append(coin)

    # Log failed coins (all of them, not just the first 5)
    if failed_coins:
        logger.warning(
            "[Bootstrap] %d coin(s) failed to load: %s",
            len(failed_coins),
            ", ".join(failed_coins[:20]) + (" …" if len(failed_coins) > 20 else ""),
        )

    total   = len(coins)
    avg_len = sum(hist_lens) / len(hist_lens) if hist_lens else 0.0
    min_len = min(hist_lens) if hist_lens else 0
    t_end   = _time.monotonic()

    result = BootstrapResult(
        coins_attempted = total,
        coins_loaded    = loaded,
        coins_failed    = total - loaded - skipped,
        coins_skipped   = skipped,
        avg_history_len = avg_len,
        min_history_len = min_len,
        ema_ready       = min_len >= _READY_EMA,
        mtf_ready       = min_len >= _READY_MTF_1H,
        phase5_ready    = min_len >= _READY_P5,
        duration_s      = t_end - t_start,
        failed_coins    = failed_coins,
    )
    for line in result.summary_lines():
        logger.info(line)
    return result


# =============================================================================
# PHASE 5 QUALITY SCORING
# =============================================================================

@dataclass(frozen=True)
class Phase5Score:
    trend_quality: int
    pullback_quality: int
    momentum: int
    risk_reward: int
    total: int


def phase5_score(history: list) -> Phase5Score:
    prices  = [item["price"]  for item in history]
    volumes = [item["volume"] for item in history]
    # BUG-18: EMA(21) is computed below for trend_quality; it requires at least
    # PHASE5_MIN_HISTORY ticks to produce a reliable value. With fewer ticks the
    # EMA warm-up effect causes ema_sep to be noisy, inflating trend_quality and
    # therefore final_score. Return a zero Phase5Score to prevent warm-up signals.
    if len(prices) < PHASE5_MIN_HISTORY:
        return Phase5Score(0, 0, 0, 0, 0)

    window = prices[-20:] if len(prices) >= 20 else prices
    up_moves = sum(1 for i in range(1, len(window)) if window[i] > window[i - 1])
    consistency = up_moves / (len(window) - 1) if len(window) > 1 else 0.0
    fast_e = ema(prices, EMA_FAST_PERIOD)
    slow_e = ema(prices, EMA_SLOW_PERIOD)
    ema_sep = (fast_e[-1] - slow_e[-1]) / slow_e[-1] * 100 if slow_e[-1] else 0.0
    ema_sep_score = _clamp(ema_sep / 2.0, 0.0, 1.0)
    tq_raw = (consistency * 0.6 + ema_sep_score * 0.4) * 25
    trend_quality = int(round(_clamp(tq_raw, 0, 25)))

    pb_window = prices[-10:] if len(prices) >= 10 else prices
    if len(pb_window) >= 4:
        swing_high = max(pb_window[:-2])
        swing_low  = min(pb_window[1:-1])
        current_p  = pb_window[-1]
        prior_base = pb_window[0]
        leg_size = swing_high - prior_base
        if leg_size > 0 and swing_high > swing_low:
            retracement = (swing_high - swing_low) / leg_size
            ideal = 0.38
            deviation = abs(retracement - ideal) / ideal
            pb_raw = _clamp(1.0 - deviation, 0.0, 1.0)
            recovered = 1.0 if current_p > swing_low + (swing_high - swing_low) * 0.5 else 0.4
            pullback_quality = int(round(_clamp(pb_raw * recovered * 25, 0, 25)))
        else:
            pullback_quality = 0
    else:
        pullback_quality = 0

    roc_3 = percent_change(prices[-4], prices[-1]) if len(prices) >= 4 else 0.0
    roc_score = _clamp(roc_3 / 6.0, 0.0, 1.0)
    recent_vol = average(volumes[-3:]) if len(volumes) >= 3 else volumes[-1]
    base_vol   = average(volumes[-13:-3]) if len(volumes) >= 13 else average(volumes)
    vol_acc    = _clamp((recent_vol / base_vol - 1.0) / 2.0, 0.0, 1.0) if base_vol else 0.0
    acc = 0.0
    if len(prices) >= 3:
        move_now  = prices[-1] - prices[-2]
        move_prev = prices[-2] - prices[-3]
        if move_prev != 0:
            acc = _clamp(move_now / abs(move_prev) - 1.0, 0.0, 1.0)
    mom_raw = (roc_score * 0.5 + vol_acc * 0.3 + acc * 0.2) * 25
    momentum = int(round(_clamp(mom_raw, 0, 25)))

    rr_window   = prices[-15:] if len(prices) >= 15 else prices
    recent_low  = min(rr_window)
    recent_high = max(rr_window)
    cur = prices[-1]
    risk   = cur - recent_low  if cur > recent_low  else 0.0
    reward = recent_high - cur if recent_high > cur else (cur * 0.03)
    if risk > 0:
        rr_ratio = reward / risk
        rr_raw = _clamp(rr_ratio / 3.0, 0.0, 1.0) * 25
    else:
        rr_raw = 12.5
    risk_reward = int(round(_clamp(rr_raw, 0, 25)))

    total = trend_quality + pullback_quality + momentum + risk_reward
    return Phase5Score(trend_quality=trend_quality, pullback_quality=pullback_quality, momentum=momentum, risk_reward=risk_reward, total=total)


# =============================================================================
# MULTI-TIMEFRAME ANALYSIS
# =============================================================================

_mtf_counts: dict[str, int] = {"5m_only": 0, "15m_only": 0, "5m_15m": 0, "5m_15m_1h": 0, "none": 0}
_mtf_debug: dict[str, int] = {}
_mtf_failures: list = []


def _frame_bullish(all_prices: list, window: int) -> bool:
    if len(all_prices) < 2:
        return False
    slice_prices = all_prices[-window:] if len(all_prices) >= window else all_prices
    if len(slice_prices) < 2:
        return False
    if len(all_prices) >= EMA_SLOW_PERIOD:
        fast_vals = ema(all_prices, EMA_FAST_PERIOD)
        slow_vals = ema(all_prices, EMA_SLOW_PERIOD)
        ema_bullish = fast_vals[-1] > slow_vals[-1]
    else:
        ema_bullish = all_prices[-1] > all_prices[0]
    momentum_bullish = slice_prices[-1] > slice_prices[0]
    return ema_bullish and momentum_bullish


def multi_timeframe_check(history: list) -> dict:
    prices = [item["price"] for item in history]
    n = len(prices)
    # BUG-21: gate each timeframe against its own window size.
    # _frame_bullish falls back to prices[-1] > prices[0] when len < EMA_SLOW_PERIOD,
    # which is not a meaningful 15m or 1h check on 2-23 ticks. A timeframe is only
    # trusted when the history is at least as long as its window.
    tf_5m  = _frame_bullish(prices, MTF_5M_WINDOW)  if n >= MTF_5M_WINDOW  else False
    tf_15m = _frame_bullish(prices, MTF_15M_WINDOW) if n >= MTF_15M_WINDOW else False
    tf_1h  = _frame_bullish(prices, MTF_1H_WINDOW)  if n >= MTF_1H_WINDOW  else False

    _mtf_debug["coins_checked"] = _mtf_debug.get("coins_checked", 0) + 1
    if len(prices) < MTF_5M_WINDOW:
        _mtf_debug["insufficient_history"] = _mtf_debug.get("insufficient_history", 0) + 1
    if tf_5m:  _mtf_debug["5m_bullish"]  = _mtf_debug.get("5m_bullish",  0) + 1
    if tf_15m: _mtf_debug["15m_bullish"] = _mtf_debug.get("15m_bullish", 0) + 1
    if tf_1h:  _mtf_debug["1h_bullish"]  = _mtf_debug.get("1h_bullish",  0) + 1
    if tf_5m and tf_15m and tf_1h:
        _mtf_debug["full_alignment"] = _mtf_debug.get("full_alignment", 0) + 1

    if tf_5m and tf_15m and tf_1h:
        alignment = "5m_15m_1h"
    elif tf_5m and tf_15m:
        alignment = "5m_15m"
    elif tf_5m and not tf_15m:
        alignment = "5m_only"
    elif tf_15m and not tf_5m:
        alignment = "15m_only"
    else:
        alignment = "none"

    _mtf_counts[alignment] = _mtf_counts.get(alignment, 0) + 1

    if alignment == "none" and len(_mtf_failures) < 10:
        _mtf_failures.append({
            "history_len": len(prices),
            "tf_5m": tf_5m, "tf_15m": tf_15m, "tf_1h": tf_1h,
            "last_price": prices[-1] if prices else None,
            "first_price": prices[0] if prices else None,
        })

    return {
        "tf_5m_bull":   tf_5m,
        "tf_15m_bull":  tf_15m,
        "tf_1h_bull":   tf_1h,
        "candidate_ok": tf_5m or tf_15m,
        "strong_ok":    tf_5m and tf_15m,
        "premium_ok":   tf_5m and tf_15m and tf_1h,
        "alignment":    alignment,
    }


# =============================================================================
# MARKET STATE ENGINE
# =============================================================================

def detect_market_state(history: list) -> str:
    if len(history) < 6:
        return "sideways"
    prices  = [item["price"]  for item in history]
    volumes = [item["volume"] for item in history]

    ema_bull = False
    if len(prices) >= EMA_SLOW_PERIOD:
        fast_e = ema(prices, EMA_FAST_PERIOD)
        slow_e = ema(prices, EMA_SLOW_PERIOD)
        ema_bull = fast_e[-1] > slow_e[-1]
    else:
        ema_bull = prices[-1] > prices[0]

    momentum_3 = percent_change(prices[-4], prices[-1]) if len(prices) >= 4 else percent_change(prices[0], prices[-1])
    momentum_positive = momentum_3 > 0.3
    momentum_negative = momentum_3 < -0.3

    recent_vol   = average(volumes[-3:]) if len(volumes) >= 3 else volumes[-1]
    baseline_vol = average(volumes[-13:-3]) if len(volumes) >= 13 else average(volumes)
    vol_ratio    = recent_vol / baseline_vol if baseline_vol else 1.0
    vol_spike    = vol_ratio > VOLUME_SPIKE_MULTIPLIER

    window = prices[-12:] if len(prices) >= 12 else prices
    n = len(window)
    first_half  = window[:n // 2]
    second_half = window[n // 2:]

    prev_high = max(first_half); prev_low = min(first_half)
    curr_high = max(second_half); curr_low = min(second_half)

    higher_highs = curr_high > prev_high
    higher_lows  = curr_low  > prev_low
    lower_highs  = curr_high < prev_high
    lower_lows   = curr_low  < prev_low

    range_size   = curr_high - curr_low
    pos_in_range = (prices[-1] - curr_low) / range_size if range_size > 0 else 0.5
    near_bottom  = pos_in_range < 0.25

    lookback      = prices[-20:] if len(prices) >= 20 else prices
    new_local_high = prices[-1] >= max(lookback)

    if new_local_high and vol_spike and momentum_positive:
        return "breakout"
    if ema_bull and higher_highs and higher_lows and momentum_positive:
        return "bull_trend"
    if ema_bull and near_bottom and not momentum_positive:
        return "pullback"
    if ema_bull and not (higher_highs and higher_lows) and momentum_positive:
        return "recovery"
    if not ema_bull and lower_highs and lower_lows and momentum_negative:
        return "downtrend"
    return "sideways"


# =============================================================================
# OPPORTUNITY TYPE ENGINE
# =============================================================================

_OPP_BASE: dict[str, str] = {
    "bull_trend": "continuation",
    "pullback":   "accumulation",
    "recovery":   "recovery_trade",
    "breakout":   "momentum_trade",
    "sideways":   "watchlist",
    "downtrend":  "avoid",
}

_OPP_LABELS: list = [
    "accumulation", "recovery_trade", "momentum_trade",
    "continuation", "watchlist", "avoid",
]

PRIORITY_LEVELS: list = [
    (90, "Elite"),
    (80, "High"),
    (70, "Medium"),
    (60, "Watch"),
    (0,  "Ignore"),
]

_CLASS_BONUS: dict[str, int] = {"A": 20, "B": 10, "C": 0}

_OPP_TYPE_BONUS: dict[str, int] = {
    "continuation":   15,
    "recovery_trade": 12,
    "accumulation":   10,
    "momentum_trade":  8,
    "watchlist":       0,
    "avoid":         -50,
}

_MTF_BONUS: dict[str, int] = {
    "5m_15m_1h": 20,
    "5m_15m":    10,
    "5m_only":    5,
    "15m_only":   5,
    "none":       0,
}


def calculate_risk_level(coin_class: str, opportunity_type: str, mtf_alignment: str, confidence: int) -> str:
    if opportunity_type == "avoid":
        return "high"
    full_mtf   = mtf_alignment == "5m_15m_1h"
    strong_mtf = mtf_alignment == "5m_15m"
    if coin_class == "A" and (full_mtf or strong_mtf):
        base = "low"
    elif coin_class == "A":
        base = "medium"
    elif coin_class == "B":
        base = "medium"
    elif opportunity_type == "momentum_trade":
        base = "high"
    else:
        base = "medium"
    if confidence < 60:
        if base == "low":
            return "medium"
        return "high"
    return base


def priority_from_score(opp_score: int) -> str:
    for threshold, label in PRIORITY_LEVELS:
        if opp_score >= threshold:
            return label
    return "Ignore"


def calculate_opportunity_score(coin_class: str, opportunity_type: str, confidence: int, mtf_alignment: str, historical_score: int) -> int:
    if opportunity_type == "avoid":
        return 0
    score = 30
    score += _CLASS_BONUS.get(coin_class, 0)
    score += _OPP_TYPE_BONUS.get(opportunity_type, 0)
    score += _MTF_BONUS.get(mtf_alignment, 0)
    score += min(confidence // 10, 10)
    score += min(historical_score // 10, 10)
    return max(0, min(100, score))


def detect_opportunity_type(market_state: str, coin_class: str, phase5_total: int, mtf_alignment: str) -> tuple:
    if market_state == "downtrend":
        return ("avoid", 0)
    opp_type   = _OPP_BASE.get(market_state, "watchlist")
    confidence = 40
    if coin_class == "A":
        confidence += 25
    elif coin_class == "B":
        confidence += 15
    if mtf_alignment == "5m_15m_1h":
        confidence += 20
    elif mtf_alignment == "5m_15m":
        confidence += 12
    elif mtf_alignment in ("5m_only", "15m_only"):
        confidence += 5
    if phase5_total >= 75:
        confidence += 15
    elif phase5_total >= 50:
        confidence += 8
    elif phase5_total >= 25:
        confidence += 3
    confidence = min(confidence, 100)
    return (opp_type, confidence)


# =============================================================================
# SMART FILTER / LEARNING FILTER / HISTORICAL FILTER
# =============================================================================

_filter_counts: dict[str, int] = {
    "low_score": 0, "no_volume": 0, "no_ema": 0, "no_mtf": 0,
    "smart_filter": 0, "learning_filter": 0, "historical_filter": 0,
}

_learning_avoid_keys: Optional[set] = None
_learning_recommend_keys: Optional[set] = None
_learning_cache_updated_at: float = 0.0
_LEARNING_CACHE_TTL = 3600.0


def smart_filter(signal: Signal) -> bool:
    reject = (
        signal.priority.lower() == "ignore"
        or signal.opportunity_type == "avoid"
        or (signal.risk_level.lower() == "high" and signal.opp_confidence < 60)
    )
    if reject:
        _filter_counts["smart_filter"] = _filter_counts.get("smart_filter", 0) + 1
        return False
    return True


def _build_learning_key(signal: Signal) -> str:
    return (
        signal.coin_class + "|"
        + signal.market_state + "|"
        + signal.opportunity_type + "|"
        + signal.priority
    )


def _matches_learning_key(signal_key: str, key_set: set) -> bool:
    if signal_key in key_set:
        return True
    base = "|".join(signal_key.split("|")[:3]) + "|*"
    return base in key_set


def _refresh_learning_cache(tracker) -> None:
    global _learning_avoid_keys, _learning_recommend_keys, _learning_cache_updated_at
    import time as _time
    now = _time.monotonic()
    if _learning_avoid_keys is not None and now - _learning_cache_updated_at < _LEARNING_CACHE_TTL:
        return

    def _key_from_desc(desc: str) -> str:
        try:
            parts = desc.split("-class ", 1)
            cls = parts[0].split()[-1].strip()
            rest = parts[1]
            ot_start = rest.find("("); ot_end = rest.find(")")
            ot_raw = rest[ot_start + 1:ot_end].replace(" ", "_")
            ms_raw = rest[:ot_start].strip().replace(" ", "_")
            return cls + "|" + ms_raw + "|" + ot_raw + "|*"
        except Exception:
            return ""

    recs = tracker.learning_recommendations()
    _learning_avoid_keys     = {_key_from_desc(d) for d in recs.get("avoid", [])}
    _learning_recommend_keys = {_key_from_desc(d) for d in recs.get("recommended", [])}
    _learning_cache_updated_at = now


def learning_filter(signal: Signal, tracker) -> bool:
    _refresh_learning_cache(tracker)
    if not _learning_avoid_keys:
        return True
    key = _build_learning_key(signal)
    if not key:
        return True
    in_avoid     = _matches_learning_key(key, _learning_avoid_keys)
    in_recommend = _matches_learning_key(key, _learning_recommend_keys or set())
    if in_avoid and not in_recommend:
        _filter_counts["learning_filter"] = _filter_counts.get("learning_filter", 0) + 1
        return False
    return True


HIST_FILTER_REJECT_BELOW = -50.0
HIST_FILTER_FLAG_ABOVE   = 100.0


def historical_filter(signal: Signal) -> bool:
    p90 = signal.exch_perf_90d  # now exists on Signal dataclass
    if p90 is None:
        return True
    if p90 < HIST_FILTER_REJECT_BELOW:
        _filter_counts["historical_filter"] = _filter_counts.get("historical_filter", 0) + 1
        logger.debug("historical_filter rejected %s: exch_perf_90d=%.2f%%", signal.coin, p90)
        return False
    if p90 > HIST_FILTER_FLAG_ABOVE:
        logger.debug("historical_filter flagged %s: exch_perf_90d=%.2f%% (parabolic)", signal.coin, p90)
    return True


# =============================================================================
# SIGNAL TIER / FORMATTERS
# =============================================================================

def signal_tier(score: int) -> str:
    if score >= 85: return "PREMIUM"
    if score >= 70: return "STRONG SIGNAL"
    if score >= 60: return "CANDIDATE"
    return "IGNORE"


def format_price(price: float) -> str:
    text = f"{price:,.8f}".rstrip("0").rstrip(".")
    return text if text else "0"


def format_volume(volume: float) -> str:
    return f"{volume:,.2f}"


def format_percent(value: Optional[float]) -> str:
    if value is None:
        return "pending"
    return f"{value:+.2f}%"


# =============================================================================
# ANALYZE COIN  (core signal generation — UNCHANGED)
# =============================================================================

def analyze_coin(coin: str, history: list, market: str = "INR") -> list:
    # BUG-16: the original gate (< 2) allowed indicator calculations on as few
    # as 2 ticks. EMA crossover, volume baseline, and MTF checks all require
    # significantly more history to produce reliable values. With fewer than
    # ANALYZE_MIN_HISTORY ticks the EMA warm-up effect generates false crossovers
    # and 1-sample volume baselines trigger spurious volume spikes.
    if len(history) < ANALYZE_MIN_HISTORY:
        return []

    mtf = multi_timeframe_check(history)
    if not mtf["candidate_ok"]:
        _filter_counts["no_mtf"] = _filter_counts.get("no_mtf", 0) + 1
        return []

    prices = [item["price"] for item in history]
    volumes = [item["volume"] for item in history]
    current_price  = prices[-1]
    current_volume = volumes[-1]
    created_at = datetime.now(timezone.utc)
    score = 0
    reasons: list = []

    fast = ema(prices, EMA_FAST_PERIOD)
    slow = ema(prices, EMA_SLOW_PERIOD)
    recent_move      = percent_change(prices[-2], current_price)
    momentum_strength = max(recent_move, 0.0)

    previous_volumes = volumes[-(VOLUME_AVERAGE_PERIOD + 1):-1]
    average_volume   = average(previous_volumes)
    volume_strength  = current_volume / average_volume if average_volume else 0.0

    # Gate 1: EMA crossover is MANDATORY
    has_ema_crossover = False
    if len(fast) >= 2 and len(slow) >= 2:
        crossed_up   = fast[-2] <= slow[-2] and fast[-1] > slow[-1]
        crossed_down = fast[-2] >= slow[-2] and fast[-1] < slow[-1]
        if crossed_up or crossed_down:
            has_ema_crossover = True
            score += 25
            reasons.append("EMA crossover")
        if fast[-1] > slow[-1] and recent_move > 0:
            score += 10
            reasons.append("Strong trend")

    if not has_ema_crossover:
        _filter_counts["no_ema"] += 1
        return []

    # Gate 2: Volume spike is MANDATORY
    has_volume_spike = volume_strength > VOLUME_SPIKE_MULTIPLIER
    if has_volume_spike:
        score += 20
        reasons.append("Volume spike")
    else:
        _filter_counts["no_volume"] += 1
        return []

    if recent_move >= MOMENTUM_THRESHOLD_PERCENT:
        score += 15
        reasons.append("Positive momentum")

    # BUG-19: the baseline window needs VOLATILITY_LOOKBACK*2+1 ticks (41) to be
    # non-degenerate. With 22-40 ticks the slice resolves to as few as 2 items,
    # making the baseline meaningless and firing false breakout signals.
    if len(prices) >= VOLATILITY_MIN_HISTORY:
        current_volatility = volatility(prices[-(VOLATILITY_LOOKBACK + 1):])
        baseline = volatility(prices[-(VOLATILITY_LOOKBACK * 2 + 1):-VOLATILITY_LOOKBACK])
        if baseline and current_volatility > baseline * VOLATILITY_SPIKE_MULTIPLIER:
            score += 10
            reasons.append("High volatility breakout")

    # Gate 3: Minimum score
    if score < 60:
        _filter_counts["low_score"] += 1
        return []

    # MTF tier/bonus
    alignment = mtf["alignment"]
    if alignment == "5m_15m_1h":
        score = min(score + 10, 100)
        reasons.append("MTF: 5m+15m+1h aligned ⭐")
        effective_tier = signal_tier(score)
    elif alignment == "5m_15m":
        score = min(score + 5, 100)
        reasons.append("MTF: 5m+15m aligned")
        raw_tier = signal_tier(score)
        effective_tier = "STRONG SIGNAL" if raw_tier == "PREMIUM" else raw_tier
    elif alignment == "5m_only":
        reasons.append("MTF: 5m bullish only")
        effective_tier = "CANDIDATE"
    else:
        reasons.append("MTF: 15m bullish only")
        effective_tier = "CANDIDATE"

    tier         = effective_tier
    coin_class   = get_coin_class(coin)
    market_state = detect_market_state(history)
    p5           = phase5_score(history)
    # OPT-1: fetch daily closes once; reuse for both historical_pattern_score
    # and get_historical_performance to eliminate the duplicate candle API call.
    _daily_closes = _fetch_coin_closes(coin)
    hist         = historical_pattern_score(coin, current_price, closes=_daily_closes)

    scanner_norm = min(score, 100)
    final_score  = int(round(scanner_norm * 0.40 + p5.total * 0.40 + hist.total * 0.20))

    opportunity_type, opp_confidence = detect_opportunity_type(
        market_state=market_state,
        coin_class=coin_class,
        phase5_total=p5.total,
        mtf_alignment=mtf.get("alignment", "none"),
    )

    opportunity_score = calculate_opportunity_score(
        coin_class=coin_class,
        opportunity_type=opportunity_type,
        confidence=opp_confidence,
        mtf_alignment=mtf.get("alignment", "none"),
        historical_score=hist.total,
    )

    priority   = priority_from_score(opportunity_score)
    risk_level = calculate_risk_level(
        coin_class=coin_class,
        opportunity_type=opportunity_type,
        mtf_alignment=mtf.get("alignment", "none"),
        confidence=opp_confidence,
    )

    # Fetch 90-day exchange performance — reuses pre-fetched closes (OPT-1)
    perf_data    = get_historical_performance(coin, closes=_daily_closes)
    exch_perf_90d = perf_data.get("perf_90d")

    return [
        Signal(
            coin=coin,
            kind=tier.lower().replace(" ", "_"),
            score=score,
            message="; ".join(reasons),
            price=current_price,
            volume=current_volume,
            created_at=created_at,
            tier=tier,
            reasons=reasons,
            volume_strength=volume_strength,
            momentum_strength=momentum_strength,
            model_version=MODEL_VERSION,
            phase5_trend=p5.trend_quality,
            phase5_pullback=p5.pullback_quality,
            phase5_momentum=p5.momentum,
            phase5_risk_reward=p5.risk_reward,
            phase5_total=p5.total,
            final_score=final_score,
            hist_trend_7d=hist.trend_7d,
            hist_trend_30d=hist.trend_30d,
            hist_trend_90d=hist.trend_90d,
            hist_sr_quality=hist.sr_quality,
            hist_vol_score=hist.hist_vol,
            hist_total=hist.total,
            coin_class=coin_class,
            market_state=market_state,
            opportunity_type=opportunity_type,
            opp_confidence=opp_confidence,
            opportunity_score=opportunity_score,
            priority=priority,
            risk_level=risk_level,
            market=market,  # I-10: track which market (INR/USDT) the signal was generated from
            exch_perf_90d=exch_perf_90d,
        )
    ]


# =============================================================================
# PUBLIC MARKET DATA CLIENT
# =============================================================================

class CoinDCXPublicClient:
    """
    Thin HTTP client for CoinDCX public endpoints.

    SP1.2 fixes applied:
    - fetch_tickers retries up to _TICKER_MAX_RETRIES times on transient
      network errors (Timeout, ConnectionError) before raising.
    - Response is validated: must be a non-empty list of dicts.
    - JSONDecodeError on malformed responses is caught and re-raised as
      ValueError so callers can treat it uniformly.
    """

    _TICKER_MAX_RETRIES   = 3
    _TICKER_RETRY_DELAY_S = 5.0

    def fetch_tickers(self) -> list:
        last_exc: Exception | None = None
        for attempt in range(1, self._TICKER_MAX_RETRIES + 1):
            try:
                response = _limited_get(
                    COINDCX_TICKER_URL,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
                response.raise_for_status()

                try:
                    data = response.json()
                except Exception as exc:
                    raise ValueError(
                        f"Ticker API returned non-JSON body: {response.text[:200]}"
                    ) from exc

                # Validate structure
                if not isinstance(data, list):
                    raise ValueError(
                        f"Ticker API returned unexpected type {type(data).__name__} (expected list)"
                    )
                if len(data) == 0:
                    raise ValueError("Ticker API returned empty list")

                # Validate at least one entry is a dict with 'market'
                has_valid_entry = any(
                    isinstance(t, dict) and t.get("market") for t in data[:10]
                )
                if not has_valid_entry:
                    raise ValueError(
                        f"Ticker API response has no valid market entries (first entry: {data[0] if data else 'N/A'})"
                    )

                logger.debug(
                    "[Ticker] fetched %d tickers (attempt %d/%d)",
                    len(data), attempt, self._TICKER_MAX_RETRIES,
                )
                return data

            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                last_exc = exc
                logger.warning(
                    "[Ticker] network error on attempt %d/%d: %s",
                    attempt, self._TICKER_MAX_RETRIES, exc,
                )
                if attempt < self._TICKER_MAX_RETRIES:
                    time.sleep(self._TICKER_RETRY_DELAY_S)

            except (requests.exceptions.HTTPError, ValueError) as exc:
                # HTTP errors (4xx/5xx) and validation errors are not retried
                logger.warning("[Ticker] non-retryable error: %s", exc)
                raise

        # All retries exhausted
        logger.error(
            "[Ticker] all %d retries failed — last error: %s",
            self._TICKER_MAX_RETRIES, last_exc,
        )
        raise last_exc


# =============================================================================
# SIGNAL PERFORMANCE TRACKER
# =============================================================================

class SignalPerformanceTracker:
    def __init__(self, path: str = SIGNAL_LOG_FILE, stats_path: str = STATS_FILE):
        self.path       = Path(path)
        self.stats_path = Path(stats_path)
        self._data      = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"signals": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            backup_path = self.path.with_suffix(self.path.suffix + ".bak")
            if backup_path.exists():
                try:
                    backup_data = json.loads(backup_path.read_text(encoding="utf-8"))
                    if isinstance(backup_data, dict):
                        backup_data.setdefault("signals", [])
                        write_json_safely(self.path, backup_data)
                        logger.warning("Recovered signal history from backup: %s", backup_path)
                        return backup_data
                except (OSError, json.JSONDecodeError):
                    logger.warning("Could not recover signal history from backup: %s", backup_path, exc_info=True)
            return {"signals": []}
        if not isinstance(data, dict):
            return {"signals": []}
        data.setdefault("signals", [])
        return data

    def save(self) -> None:
        write_json_safely(self.path, self._data)

    def save_stats(self) -> None:
        try:
            write_json_safely(self.stats_path, self.stats())
        except Exception:
            pass

    def log_signal(self, signal: Signal) -> None:
        if self._is_duplicate(signal):
            return
        self._data["signals"].append({
            "id":         f"{signal.coin}-{int(signal.created_at.timestamp())}-{signal.kind}",
            "timestamp":  signal.created_at.isoformat(),
            "coin":       signal.coin,
            "category":   signal.tier.title(),
            "score":      signal.score,
            "signal_price": signal.price,
            "reasons":    signal.reasons,
            "model_version": signal.model_version,
            "phase5": {
                "trend_quality":    signal.phase5_trend,
                "pullback_quality": signal.phase5_pullback,
                "momentum":         signal.phase5_momentum,
                "risk_reward":      signal.phase5_risk_reward,
                "total":            signal.phase5_total,
            },
            "final_score":      signal.final_score,
            "coin_class":       signal.coin_class,
            "market_state":     signal.market_state,
            "opportunity_type": signal.opportunity_type,
            "opp_confidence":   signal.opp_confidence,
            "opportunity_score": signal.opportunity_score,
            "priority":         signal.priority,
            "risk_level":       signal.risk_level,
            "historical_score": {
                "trend_7d":   signal.hist_trend_7d,
                "trend_30d":  signal.hist_trend_30d,
                "trend_90d":  signal.hist_trend_90d,
                "sr_quality": signal.hist_sr_quality,
                "hist_vol":   signal.hist_vol_score,
                "total":      signal.hist_total,
            },
            "evaluations": {},
        })
        if len(self._data["signals"]) > MAX_SIGNALS:
            self._data["signals"] = self._data["signals"][-MAX_SIGNALS:]
        self.save()

    def evaluate_due_signals(self, prices: dict) -> int:
        now     = datetime.now(timezone.utc)
        updated = 0
        for item in self._data["signals"]:
            signal_time = self._parse_time(item.get("timestamp"))
            if signal_time is None:
                continue
            coin          = item.get("coin")
            current_price = prices.get(coin)
            if current_price is None or current_price <= 0:
                continue
            signal_price = self._to_float(item.get("signal_price"))
            if signal_price <= 0:
                continue
            evaluations = item.setdefault("evaluations", {})
            age = (now - signal_time).total_seconds()
            for label, seconds in EVALUATION_HORIZONS.items():
                if age >= seconds and label not in evaluations:
                    change = percent_change(signal_price, current_price)
                    evaluations[label] = {
                        "timestamp": now.isoformat(),
                        "price":     current_price,
                        "change_percent": change,
                    }
                    updated += 1
        if updated:
            self.save()
            self.save_stats()
            # Append any newly-evaluated signals to the permanent history
            for item in self._data["signals"]:
                if not item.get("evaluations"):
                    continue
                ev = item["evaluations"]
                # Build the most complete record we have
                latest = None
                for label in ("7d", "3d", "24h", "4h", "1h"):
                    if label in ev:
                        latest = ev[label]
                        break
                if not latest:
                    continue
                change = latest.get("change_percent", 0)
                entry = {
                    "id": item.get("id"),
                    "coin": item.get("coin"),
                    "timestamp": item.get("timestamp"),
                    "score": item.get("score"),
                    "tier": item.get("category"),
                    "signal_price": item.get("signal_price"),
                    "market_state": item.get("market_state"),
                    "1h_pct": round(ev.get("1h", {}).get("change_percent", 0), 2),
                    "4h_pct": round(ev.get("4h", {}).get("change_percent", 0), 2),
                    "24h_pct": round(ev.get("24h", {}).get("change_percent", 0), 2),
                    "3d_pct": round(ev.get("3d", {}).get("change_percent", 0), 2),
                    "7d_pct": round(ev.get("7d", {}).get("change_percent", 0), 2),
                    "return_pct": round(change, 2),
                    "result": "WIN" if change > 0 else "LOSS",
                    "evaluated_at": latest.get("timestamp"),
                }
                # I-01: Only update performance if signal was actually added (not duplicate)
                added = append_signal_history(entry)
                if added:
                    update_coin_performance(entry)
                    update_tier_accuracy(entry)
        return updated

    def stats(self) -> dict:
        signals        = self._data["signals"]
        latest_changes = [self._latest_change(item) for item in signals]
        completed      = [c for c in latest_changes if c is not None]
        winners        = sum(1 for c in completed if c > 0)
        losers         = sum(1 for c in completed if c <= 0)
        return {
            "last_updated":   datetime.now(timezone.utc).isoformat(),
            "total_signals":  len(signals),
            "winning_signals": winners,
            "losing_signals":  losers,
            "win_rate":       (winners / len(completed) * 100) if completed else 0.0,
        }

    def learning_recommendations(self) -> dict:
        return {"recommended": [], "avoid": [], "has_data": False}

    def top_ranked_signals(self, limit: int = 10) -> list:
        ranked = []
        for item in self._data["signals"]:
            opp_sc = item.get("opportunity_score")
            if opp_sc is None:
                continue
            pri = item.get("priority", "Ignore")
            if pri.lower() == "ignore":
                continue
            ranked.append(item)
        ranked.sort(
            key=lambda s: (
                self._to_float(s.get("opportunity_score", 0)),
                self._to_float(s.get("opp_confidence", 0)),
                s.get("timestamp", ""),
            ),
            reverse=True,
        )
        return ranked[:limit]

    def recent_signals(self, limit: int = 10) -> list:
        return sorted(self._data["signals"], key=lambda i: i.get("timestamp", ""), reverse=True)[:limit]

    def _latest_change(self, item: dict) -> Optional[float]:
        evaluations = item.get("evaluations", {})
        for label in ("24h", "4h", "1h"):
            if label in evaluations:
                return self._to_float(evaluations[label].get("change_percent"))
        return None

    def _is_duplicate(self, signal: Signal) -> bool:
        signal_minute = signal.created_at.replace(second=0, microsecond=0).isoformat()
        for item in self._data["signals"][-50:]:
            item_time = self._parse_time(item.get("timestamp"))
            if item_time is None:
                continue
            item_minute = item_time.replace(second=0, microsecond=0).isoformat()
            if item.get("coin") == signal.coin and item.get("score") == signal.score and item_minute == signal_minute:
                return True
        return False

    @staticmethod
    def _parse_time(value) -> Optional[datetime]:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    @staticmethod
    def _to_float(value) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0


# =============================================================================
# ASYNC SCANNER
# =============================================================================

class Scanner:
    def __init__(
        self,
        watchlist_store: WatchlistStore,
        alert_callback: Callable,
        performance_tracker: SignalPerformanceTracker,
        client: Optional[CoinDCXPublicClient] = None,
    ):
        self.watchlist_store     = watchlist_store
        self.alert_callback      = alert_callback
        self.performance_tracker = performance_tracker
        self.client              = client or CoinDCXPublicClient()

        self.price_history: dict[str, list] = defaultdict(list)
        self.last_alert_at: dict[str, datetime] = {}

        self._alert_in_flight: set  = set()
        self._alert_lock            = asyncio.Lock()
        self._scan_semaphore        = asyncio.Semaphore(SCAN_CONCURRENCY)

        self._ticker_cache: Optional[list] = None
        self._ticker_cache_at = 0.0
        self._ticker_lock = asyncio.Lock()

        self._bootstrap_result: Optional[BootstrapResult] = None

    async def run_bootstrap(self) -> BootstrapResult:
        if not BOOTSTRAP_ENABLED:
            logger.info("Bootstrap disabled (BOOTSTRAP_ENABLED=false)")
            self._bootstrap_result = BootstrapResult()
            return self._bootstrap_result

        logger.info("Bootstrap: fetching current tickers to build coin list...")
        try:
            tickers = await self.get_tickers(force=True)
        except Exception:
            logger.warning("Bootstrap: ticker fetch failed; skipping bootstrap", exc_info=True)
            self._bootstrap_result = BootstrapResult()
            return self._bootstrap_result

        ticker_map    = self._ticker_map(tickers)
        watchlist_set = set(self.watchlist_store.all())
        discovery_set = {
            coin for coin, ticker in ticker_map.items()
            if coin not in watchlist_set and self._passes_discovery_filters(ticker)
        }
        all_coins = list(watchlist_set) + list(discovery_set)[:DISCOVERY_MAX_COINS]
        logger.info("Bootstrap: loading history for %d coins", len(all_coins))

        result = await bootstrap_price_history(
            coins=all_coins,
            price_history=self.price_history,
            concurrency=BOOTSTRAP_CONCURRENCY,
        )
        self._bootstrap_result = result
        return result

    async def run_forever(self) -> None:
        discovery_due = 0.0
        logger.info(
            "Scanner started: scan_interval=%ss discovery_interval=%ss concurrency=%s",
            SCAN_INTERVAL_SECONDS, DISCOVERY_INTERVAL_SECONDS, SCAN_CONCURRENCY,
        )
        while True:
            started = asyncio.get_running_loop().time()
            try:
                tickers = await self.get_tickers(force=True)
                # OPT-3: build the ticker_map once per scan cycle and pass it
                # to all consumers so _ticker_map()'s O(N) loop runs only once.
                ticker_map = self._ticker_map(tickers)
                self.evaluate_signal_performance(tickers, ticker_map=ticker_map)
                watchlist_signals = await self.scan_watchlist(tickers, ticker_map=ticker_map)

                now = asyncio.get_running_loop().time()
                if now >= discovery_due:
                    discovery_signals = await self.scan_market(tickers, ticker_map=ticker_map)
                    discovery_due = now + DISCOVERY_INTERVAL_SECONDS
                else:
                    discovery_signals = []

                elapsed = asyncio.get_running_loop().time() - started
                logger.info(
                    "Scan complete: watchlist_signals=%s discovery_signals=%s elapsed=%.2fs",
                    len(watchlist_signals), len(discovery_signals), elapsed,
                )
            except Exception:
                logger.exception("Scanner loop failed; retrying next interval")

            elapsed = asyncio.get_running_loop().time() - started
            await asyncio.sleep(max(5, SCAN_INTERVAL_SECONDS - elapsed))

    async def get_tickers(self, force: bool = False) -> list:
        """
        Return the current ticker list, using an in-memory cache.

        SP1.2 fixes applied:
        - Catches ALL exceptions from fetch_tickers (not just RequestException|ValueError),
          ensuring JSONDecodeError and unexpected errors also fall back to stale cache.
        - Validates the fresh response is a non-empty list before caching it.
        - Returns stale cache when available regardless of how old it is
          (better than crashing the scan loop on transient API outages).
        - Raises only when no cache exists at all (first-ever fetch failure).
        """
        async with self._ticker_lock:
            now = asyncio.get_running_loop().time()
            cache_fresh = (
                self._ticker_cache is not None
                and now - self._ticker_cache_at < TICKER_CACHE_TTL_SECONDS
            )
            if cache_fresh and not force:
                try:
                    from . import monitoring_metrics as _mm
                    _mm.record_cache("ticker", hit=True)
                except Exception:
                    pass
                return self._ticker_cache or []
            try:
                from . import monitoring_metrics as _mm
                _mm.record_cache("ticker", hit=False)
            except Exception:
                pass
            try:
                tickers = await asyncio.to_thread(self.client.fetch_tickers)
                # SP1.2: validate before caching (fetch_tickers already validates,
                # but guard here too in case client is overridden in tests/subclasses)
                if not isinstance(tickers, list) or len(tickers) == 0:
                    raise ValueError(
                        f"fetch_tickers returned invalid data: {type(tickers).__name__} len={len(tickers) if isinstance(tickers, list) else 'N/A'}"
                    )
            except Exception:
                if self._ticker_cache is not None:
                    logger.warning(
                        "[Ticker] fetch failed — returning stale cache (%d tickers, age=%.0fs)",
                        len(self._ticker_cache),
                        now - self._ticker_cache_at,
                        exc_info=True,
                    )
                    return self._ticker_cache
                logger.error("[Ticker] fetch failed and no cache available — raising", exc_info=True)
                raise
            self._ticker_cache    = tickers
            self._ticker_cache_at = asyncio.get_running_loop().time()
            return tickers

    def evaluate_signal_performance(self, tickers: list, ticker_map: Optional[dict] = None) -> None:
        # OPT-3: accept pre-built ticker_map from run_forever() to avoid rebuilding it
        ticker_map = ticker_map or self._ticker_map(tickers)
        prices = {
            coin: self._extract_price_volume(ticker)[0]
            for coin, ticker in ticker_map.items()
        }
        updated = self.performance_tracker.evaluate_due_signals(prices)
        if updated:
            logger.info("Updated %s signal performance checkpoints", updated)

    async def scan_watchlist(self, tickers: Optional[list] = None, ticker_map: Optional[dict] = None) -> list:
        # OPT-3: accept pre-built ticker_map from run_forever() to avoid rebuilding it
        tickers    = tickers or await self.get_tickers()
        ticker_map = ticker_map or self._ticker_map(tickers)
        coins      = [coin for coin in self.watchlist_store.all() if coin in ticker_map]
        return await self._scan_many(coins, ticker_map, source="watchlist")

    async def scan_market(self, tickers: Optional[list] = None, ticker_map: Optional[dict] = None) -> list:
        # OPT-3: accept pre-built ticker_map from run_forever() to avoid rebuilding it
        tickers    = tickers or await self.get_tickers()
        ticker_map = ticker_map or self._ticker_map(tickers)
        watchlist  = set(self.watchlist_store.all())
        coins = [
            coin for coin, ticker in ticker_map.items()
            if coin not in watchlist and self._passes_discovery_filters(ticker)
        ][:DISCOVERY_MAX_COINS]
        return await self._scan_many(coins, ticker_map, source="discovery")

    async def coin_snapshot(self, coin: str) -> Optional[dict]:
        coin    = coin.upper().strip()
        tickers = await self.get_tickers()
        ticker  = self._ticker_map(tickers).get(coin)
        if not ticker:
            return None
        price, volume = self._extract_price_volume(ticker)
        if price <= 0:
            return None
        self._append_history(coin, price, volume)
        history = self.price_history[coin]
        return {
            "coin": coin, "price": price, "volume": volume,
            "history_count": len(history),
            "trend": trend_summary(history),
            "signals": analyze_coin(coin, history),
        }

    async def _scan_many(self, coins: list, ticker_map: dict, source: str) -> list:
        _funnel_before = dict(_filter_counts)
        tasks = [
            asyncio.create_task(self._scan_ticker_bounded(coin, ticker_map[coin], source))
            for coin in coins
        ]
        if not tasks:
            return []
        results = await asyncio.gather(*tasks, return_exceptions=True)
        signals: list[Signal] = []
        for coin, result in zip(coins, results):
            if isinstance(result, Exception):
                # BUG-22: log coin, exception type, and message as explicit structured fields
                # so diagnostics are visible even when traceback formatting is suppressed (e.g. Railway)
                logger.error(
                    "[Scan] coin=%s source=%s exception_type=%s message=%s",
                    coin,
                    source,
                    type(result).__name__,
                    result,
                    exc_info=(type(result), result, result.__traceback__),
                )
                continue
            signals.extend(result)

        top_signals = self._rank_signals(signals)[:MAX_RESULTS]
        for signal in top_signals:
            if not smart_filter(signal):
                continue
            if not learning_filter(signal, self.performance_tracker):
                continue
            if not historical_filter(signal):
                continue
            await self._send_alert_once(signal, source)

        try:
            from . import monitoring_metrics as _mm
            _no_volume  = _filter_counts.get("no_volume", 0) - _funnel_before.get("no_volume", 0)
            _no_ema     = _filter_counts.get("no_ema", 0) - _funnel_before.get("no_ema", 0)
            _low_score  = _filter_counts.get("low_score", 0) - _funnel_before.get("low_score", 0)
            _coins_scanned = len(coins)
            _passed_volume = max(0, _coins_scanned - _no_volume)
            _passed_trend  = max(0, _passed_volume - _no_ema)
            _passed_score  = max(0, _passed_trend - _low_score)
            _mm.record_funnel(
                coins_scanned=_coins_scanned,
                passed_volume=_passed_volume,
                passed_trend=_passed_trend,
                passed_score=_passed_score,
                signals_generated=len(top_signals),
            )
            _mm.log_event(f"{source.capitalize()} scan: {_coins_scanned} coins -> {len(top_signals)} signals")
        except Exception:
            pass
        return top_signals

    async def _scan_ticker_bounded(self, coin: str, ticker: dict, source: str) -> list:
        async with self._scan_semaphore:
            _t0 = time.monotonic()
            try:
                return await self._scan_ticker(coin, ticker, source)
            finally:
                try:
                    from . import monitoring_metrics as _mm
                    _mm.record_coin_timing(coin, (time.monotonic() - _t0) * 1000)
                except Exception:
                    pass

    async def _scan_ticker(self, coin: str, ticker: dict, source: str) -> list:
        price, volume = self._extract_price_volume(ticker)
        if price <= 0:
            return []
        # I-10: extract market from ticker (e.g. "BTCINR" → "INR")
        market = self._extract_market_from_ticker(ticker)
        self._append_history(coin, price, volume)
        return await asyncio.to_thread(analyze_coin, coin, self.price_history[coin], market)

    def _extract_market_from_ticker(self, ticker: dict) -> str:
        """I-10: extract the quote currency from the ticker market name."""
        market = str(ticker.get("market", "")).upper()
        for quote in QUOTE_PRIORITY:
            if market.endswith(quote) and len(market) > len(quote):
                return quote
        return "USDT"  # safest fallback

    async def _send_alert_once(self, signal: Signal, source: str) -> None:
        async with self._alert_lock:
            if signal.coin in self._alert_in_flight:
                return
            if not self._cooldown_passed(signal.coin):
                return
            self._alert_in_flight.add(signal.coin)
        sent = False
        try:
            self.performance_tracker.log_signal(signal)
            await self.alert_callback(signal, source)
            sent = True
        except Exception:
            logger.exception("Failed to send alert for %s", signal.coin)
        finally:
            async with self._alert_lock:
                if sent:
                    self.last_alert_at[signal.coin] = datetime.now(timezone.utc)
                self._alert_in_flight.discard(signal.coin)
        if sent:
            logger.info("Alert sent: coin=%s kind=%s source=%s score=%s", signal.coin, signal.kind, source, signal.score)

    def _append_history(self, coin: str, price: float, volume: float) -> None:
        """
        Append a live tick to the coin's price history.

        SP1.2 fix: reject ticks with price <= 0 or non-finite values to prevent
        poisoning EMA and indicator calculations with bad data.
        """
        if not (isinstance(price, (int, float)) and price > 0 and price == price and price != float("inf")):
            logger.debug(
                "[Feed] rejected invalid tick: coin=%s price=%s", coin, price
            )
            return
        history = self.price_history[coin]
        history.append({
            "time":   datetime.now(timezone.utc),
            "price":  float(price),
            "volume": max(float(volume), 0.0),
        })
        del history[:-PRICE_HISTORY_LIMIT]

    def _cooldown_passed(self, coin: str) -> bool:
        previous = self.last_alert_at.get(coin)
        if previous is None:
            return True
        age = (datetime.now(timezone.utc) - previous).total_seconds()
        return age >= ALERT_COOLDOWN_SECONDS

    def _ticker_map(self, tickers: list) -> dict:
        pairs: dict = {}
        priorities = {quote: index for index, quote in enumerate(QUOTE_PRIORITY)}
        selected_priority: dict = {}
        for ticker in tickers:
            market = str(ticker.get("market", "")).upper()
            for quote in QUOTE_PRIORITY:
                if market.endswith(quote) and len(market) > len(quote):
                    coin     = market[: -len(quote)]
                    priority = priorities[quote]
                    if coin not in pairs or priority < selected_priority[coin]:
                        pairs[coin]             = ticker
                        selected_priority[coin] = priority
                    break
        return pairs

    def _passes_discovery_filters(self, ticker: dict) -> bool:
        price, volume = self._extract_price_volume(ticker)
        if price < MIN_PRICE:
            return False
        if volume < MIN_VOLUME_24H:
            return False
        quote_vol = self._to_float(ticker.get("quote_volume") or ticker.get("volume_24h"))
        if quote_vol <= 0:
            quote_vol = volume * price
        if quote_vol < MIN_LIQUIDITY_24H:
            return False
        return True

    @staticmethod
    def _rank_signals(signals: list) -> list:
        return sorted(
            signals,
            key=lambda s: (s.final_score, s.phase5_total, s.hist_total, s.score),
            reverse=True,
        )

    def _extract_price_volume(self, ticker: dict) -> tuple:
        price  = self._to_float(ticker.get("last_price"))
        volume = self._to_float(
            ticker.get("volume") or ticker.get("volume_24h")
            or ticker.get("quote_volume") or ticker.get("base_volume")
        )
        return price, volume

    @staticmethod
    def _to_float(value) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def aggregate_market_state(self) -> str:
        """Return the most common market state across coins with enough history."""
        counts: dict[str, int] = {}
        for coin, history in self.price_history.items():
            if len(history) < 6:
                continue
            state = detect_market_state(history)
            counts[state] = counts.get(state, 0) + 1
        if not counts:
            return "sideways"
        return max(counts, key=lambda k: counts[k])
#end class        

def get_signals() -> dict:
    try:
        with open(SIGNAL_LOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning("get_signals: %s not found — returning empty fallback", SIGNAL_LOG_FILE)
    except json.JSONDecodeError as e:
        logger.warning("get_signals: JSON decode error in %s: %s — returning empty fallback", SIGNAL_LOG_FILE, e)
    except OSError as e:
        logger.warning("get_signals: OS error reading %s: %s — returning empty fallback", SIGNAL_LOG_FILE, e)
    return {"signals": []}


def get_live_signals() -> dict:
    """Return the most recent scan-cycle signals from live_signals.json."""
    try:
        with open(LIVE_SIGNALS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning("get_live_signals: %s not found — returning empty fallback", LIVE_SIGNALS_FILE)
    except json.JSONDecodeError as e:
        logger.warning("get_live_signals: JSON decode error in %s: %s — returning empty fallback", LIVE_SIGNALS_FILE, e)
    except OSError as e:
        logger.warning("get_live_signals: OS error reading %s: %s — returning empty fallback", LIVE_SIGNALS_FILE, e)
    return {"signals": []}


def get_stats() -> dict:
    try:
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning("get_stats: %s not found — returning empty fallback", STATS_FILE)
    except json.JSONDecodeError as e:
        logger.warning("get_stats: JSON decode error in %s: %s — returning empty fallback", STATS_FILE, e)
    except OSError as e:
        logger.warning("get_stats: OS error reading %s: %s — returning empty fallback", STATS_FILE, e)
    return {
        "last_updated":    None,
        "total_signals":   0,
        "winning_signals": 0,
        "losing_signals":  0,
        "win_rate":        0,
    }


def get_watchlist() -> dict:
    try:
        with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning("get_watchlist: %s not found — returning empty fallback", WATCHLIST_FILE)
    except json.JSONDecodeError as e:
        logger.warning("get_watchlist: JSON decode error in %s: %s — returning empty fallback", WATCHLIST_FILE, e)
    except OSError as e:
        logger.warning("get_watchlist: OS error reading %s: %s — returning empty fallback", WATCHLIST_FILE, e)
    return {"coins": []}


# =============================================================================
# PERSISTENT SCANNER STATE HELPERS (added for dashboard persistence)
# =============================================================================

def get_market_state() -> dict:
    try:
        with open(MARKET_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"market_state": "sideways", "strength": 50, "updated_at": None}


def get_signal_stats() -> dict:
    try:
        with open(SIGNAL_STATS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"total_signals": 0, "elite_signals": 0, "high_signals": 0, "medium_signals": 0, "updated_at": None}


def get_evaluated_signals() -> dict:
    try:
        with open(EVALUATED_SIGNALS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"evaluated_signals": []}


def save_scanner_state(signals: list, market_state: dict, stats: dict) -> None:
    """Write all scanner state to JSON files - signals, market_state, signal_stats, evaluated_signals."""
    with _scanner_state_lock:
        try:
            write_json_safely(Path(MARKET_STATE_FILE), market_state)
            write_json_safely(Path(SIGNAL_STATS_FILE), stats)
            evaluated = [s for s in signals if s.get("evaluations")]
            write_json_safely(Path(EVALUATED_SIGNALS_FILE), {"evaluated_signals": evaluated})
        except Exception:
            logger.exception("save_scanner_state: failed to write state files")


# =============================================================================
# SIGNAL PERFORMANCE TRACKER — Dashboard-facing helpers
# =============================================================================

EVALUATION_STATS_FILE = os.getenv("EVALUATION_STATS_FILE", str(STORAGE_DIR / "evaluation_stats.json"))


def get_performance_signals() -> list:
    """I-04: Return evaluated signals from signal_history.json (single source of truth).
    All dashboard pages use the same data for consistency."""
    history = get_signal_history()
    result = []
    for h in history:
        result.append({
            "coin":       h.get("coin", ""),
            "timestamp":  h.get("timestamp", ""),
            "signal_price": h.get("signal_price", 0),
            "current_price": None,
            "1h_pct":   h.get("1h_pct", 0),
            "4h_pct":   h.get("4h_pct", 0),
            "24h_pct":  h.get("24h_pct", 0),
            "3d_pct":   h.get("3d_pct", 0),
            "7d_pct":   h.get("7d_pct", 0),
            "result":   h.get("result", "LOSS"),
            "return_pct": h.get("return_pct", 0),
        })
    return result


def get_performance_stats() -> dict:
    """Return summary stats for the Performance Tracker dashboard cards."""
    signals = get_performance_signals()
    total = len(signals)
    if not total:
        return {
            "total_signals": 0,
            "winning_signals": 0,
            "losing_signals": 0,
            "win_rate_pct": 0.0,
            "avg_return_pct": 0.0,
            "best_signal": None,
            "worst_signal": None,
            "per_coin": {},
        }
    winners = [s for s in signals if s["result"] == "WIN"]
    losers  = [s for s in signals if s["result"] == "LOSS"]
    returns = [s["return_pct"] for s in signals]
    avg_return = round(sum(returns) / len(returns), 2) if returns else 0.0
    best = max(signals, key=lambda x: x["return_pct"])
    worst = min(signals, key=lambda x: x["return_pct"])
    # per-coin breakdown
    coin_map: dict = {}
    for s in signals:
        c = s["coin"]
        if c not in coin_map:
            coin_map[c] = {"signals": 0, "wins": 0, "losses": 0, "returns": []}
        coin_map[c]["signals"] += 1
        coin_map[c]["returns"].append(s["return_pct"])
        if s["result"] == "WIN":
            coin_map[c]["wins"] += 1
        else:
            coin_map[c]["losses"] += 1
    per_coin = {}
    for c, v in coin_map.items():
        total_c = v["signals"]
        per_coin[c] = {
            "signals": total_c,
            "wins": v["wins"],
            "losses": v["losses"],
            "win_rate_pct": round(v["wins"] / total_c * 100, 1) if total_c else 0.0,
            "avg_return_pct": round(sum(v["returns"]) / len(v["returns"]), 2) if v["returns"] else 0.0,
        }
    return {
        "total_signals": total,
        "winning_signals": len(winners),
        "losing_signals": len(losers),
        "win_rate_pct": round(len(winners) / total * 100, 1),
        "avg_return_pct": avg_return,
        "best_signal": {
            "coin": best["coin"],
            "return_pct": best["return_pct"],
            "timestamp": best["timestamp"],
        },
        "worst_signal": {
            "coin": worst["coin"],
            "return_pct": worst["return_pct"],
            "timestamp": worst["timestamp"],
        },
        "per_coin": per_coin,
    }


def get_per_coin_performance(coin: str) -> dict:
    """Return per-coin stats for the Performance Tracker coin breakdown."""
    stats = get_performance_stats()
    return stats.get("per_coin", {}).get(coin.upper(), {
        "signals": 0, "wins": 0, "losses": 0, "win_rate_pct": 0.0, "avg_return_pct": 0.0,
    })


# =============================================================================
# SIGNAL HISTORY — Append-only permanent record
# =============================================================================

# OPT-5: Cache signal history reads to avoid repeated disk I/O on every
# dashboard poll.  _write_history() updates the cache in-place so the next
# read is guaranteed to be consistent without a round-trip to disk.
_HISTORY_CACHE_TTL = 60.0                     # seconds between disk re-reads
_history_cache: Optional[tuple] = None        # (monotonic_time, signals_list)


def _read_history() -> list:
    """Read the signal history file; return empty list if missing.

    OPT-5: results are cached for _HISTORY_CACHE_TTL seconds to eliminate
    repeated disk reads when get_performance_stats(), get_signal_history_stats(),
    update_coin_performance(), and update_tier_accuracy() are called in the
    same dashboard poll cycle.
    """
    global _history_cache
    now = time.monotonic()
    if _history_cache is not None and now - _history_cache[0] < _HISTORY_CACHE_TTL:
        return list(_history_cache[1])
    try:
        with open(SIGNAL_HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            result = data.get("signals", [])
        elif isinstance(data, list):
            result = data
        else:
            result = []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        result = []
    _history_cache = (now, result)
    return list(result)


def _write_history(signals: list) -> None:
    """Write the full history list back to disk.

    OPT-5: update the in-memory cache immediately after a successful write so
    subsequent _read_history() calls within the TTL window see the new data
    without a disk round-trip.
    """
    global _history_cache
    with _history_lock:
        try:
            write_json_safely(Path(SIGNAL_HISTORY_FILE), {"signals": signals})
            _history_cache = (time.monotonic(), list(signals))  # keep cache fresh
        except Exception:
            logger.exception("signal_history: failed to write %s", SIGNAL_HISTORY_FILE)
            _history_cache = None  # invalidate on write failure so next read retries


def append_signal_history(entry: dict) -> bool:
    """Append a single signal record to the permanent history — never overwrites.
    Returns True if the signal was newly added, False if it was already present (deduplicated).

    Thread-safe: the full read-deduplicate-append-write sequence is held under
    _history_lock (RLock) so two concurrent callers cannot both read the same
    snapshot, both pass dedup, and both write — which would lose one entry.
    _write_history() internally re-acquires the same RLock; that is safe because
    RLock allows re-entry from the same thread.
    """
    with _history_lock:
        history = _read_history()
        # Deduplicate by id
        if entry.get("id") and any(h.get("id") == entry["id"] for h in history):
            return False
        history.append(entry)
        _write_history(history)
    return True


def get_signal_history() -> list:
    """Return the full signal history, newest first."""
    return sorted(_read_history(), key=lambda x: x.get("timestamp", ""), reverse=True)


def get_signal_history_stats() -> dict:
    """Return computed stats for the signal history."""
    history = get_signal_history()
    total = len(history)
    if not total:
        return {
            "total": 0, "winners": 0, "losers": 0,
            "win_rate_pct": 0.0, "avg_return_pct": 0.0,
        }
    winners = sum(1 for h in history if h.get("result") == "WIN")
    losers  = total - winners
    returns = [h.get("return_pct", 0) for h in history if h.get("return_pct") is not None]
    avg_return = round(sum(returns) / len(returns), 2) if returns else 0.0
    return {
        "total": total,
        "winners": winners,
        "losers": losers,
        "win_rate_pct": round(winners / total * 100, 1),
        "avg_return_pct": avg_return,
    }


# =============================================================================
# COIN-WISE PERFORMANCE — JSON-only persistent per-coin breakdown
# =============================================================================

def _read_coin_performance() -> dict:
    """Read coin_performance.json; return {} if missing."""
    try:
        with open(COIN_PERFORMANCE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _write_coin_performance(data: dict) -> None:
    """Write coin_performance.json atomically."""
    with _coin_perf_lock:
        try:
            write_json_safely(Path(COIN_PERFORMANCE_FILE), data)
        except Exception:
            logger.exception("coin_performance: failed to write %s", COIN_PERFORMANCE_FILE)


def update_coin_performance(entry: dict) -> None:
    """Update a single coin's record in coin_performance.json from a history entry."""
    coin = (entry.get("coin") or "").upper()
    if not coin:
        return
    db = _read_coin_performance()
    rec = db.get(coin, {
        "coin": coin,
        "total_signals": 0,
        "winning_signals": 0,
        "losing_signals": 0,
        "win_rate_pct": 0.0,
        "avg_return_pct": 0.0,
        "best_return_pct": 0.0,
        "worst_return_pct": 0.0,
        "last_signal_time": None,
    })
    rec["coin"] = coin
    rec["total_signals"] += 1
    if entry.get("result") == "WIN":
        rec["winning_signals"] += 1
    else:
        rec["losing_signals"] += 1
    ret = entry.get("return_pct", 0)
    # Best / worst
    if rec["total_signals"] == 1:
        rec["best_return_pct"] = ret
        rec["worst_return_pct"] = ret
    else:
        rec["best_return_pct"] = round(max(rec["best_return_pct"], ret), 2)
        rec["worst_return_pct"] = round(min(rec["worst_return_pct"], ret), 2)
    # Recalculate avg
    # We can't store sum+count in JSON, so compute from history each time
    all_history = get_signal_history()
    coin_returns = [h.get("return_pct", 0) for h in all_history if (h.get("coin") or "").upper() == coin and h.get("return_pct") is not None]
    rec["avg_return_pct"] = round(sum(coin_returns) / len(coin_returns), 2) if coin_returns else 0.0
    rec["win_rate_pct"] = round(rec["winning_signals"] / rec["total_signals"] * 100, 1)
    rec["last_signal_time"] = entry.get("timestamp") or entry.get("evaluated_at")
    db[coin] = rec
    _write_coin_performance(db)


def get_coin_performance_data() -> list:
    """Return list of all coin performance records, sorted by coin name."""
    db = _read_coin_performance()
    coins = sorted(db.keys())
    return [db[c] for c in coins]


def rebuild_coin_performance() -> dict:
    """I-01: Rebuild coin_performance.json from signal_history.json (source of truth).
    Uses a single source of truth approach to guarantee sync."""
    db = {}
    history = get_signal_history()
    for entry in history:
        coin = (entry.get("coin") or "").upper()
        if not coin:
            continue
        rec = db.get(coin, {
            "coin": coin,
            "total_signals": 0,
            "winning_signals": 0,
            "losing_signals": 0,
            "win_rate_pct": 0.0,
            "avg_return_pct": 0.0,
            "best_return_pct": 0.0,
            "worst_return_pct": 0.0,
            "last_signal_time": None,
        })
        rec["total_signals"] += 1
        if entry.get("result") == "WIN":
            rec["winning_signals"] += 1
        else:
            rec["losing_signals"] += 1
        ret = entry.get("return_pct", 0)
        if rec["total_signals"] == 1:
            rec["best_return_pct"] = ret
            rec["worst_return_pct"] = ret
        else:
            rec["best_return_pct"] = round(max(rec["best_return_pct"], ret), 2)
            rec["worst_return_pct"] = round(min(rec["worst_return_pct"], ret), 2)
        rec["last_signal_time"] = entry.get("timestamp") or entry.get("evaluated_at")
        db[coin] = rec
    # Recalculate avg and win_rate from all entries
    for coin, rec in db.items():
        returns = [h.get("return_pct", 0) for h in history if (h.get("coin") or "").upper() == coin and h.get("return_pct") is not None]
        rec["avg_return_pct"] = round(sum(returns) / len(returns), 2) if returns else 0.0
        rec["win_rate_pct"] = round(rec["winning_signals"] / rec["total_signals"] * 100, 1)
    _write_coin_performance(db)
    return db


def get_coin_performance_stats() -> dict:
    """Return aggregate stats across all coins."""
    data = get_coin_performance_data()
    total = sum(d["total_signals"] for d in data)
    wins = sum(d["winning_signals"] for d in data)
    return {
        "coins_tracked": len(data),
        "total_signals": total,
        "winning_signals": wins,
        "losing_signals": total - wins,
        "win_rate_pct": round(wins / total * 100, 1) if total else 0.0,
    }


# =============================================================================
# TIER ACCURACY — Per-tier performance tracking (JSON only)
# =============================================================================

def _read_tier_accuracy() -> dict:
    """Read tier_accuracy.json; return {} if missing."""
    try:
        with open(TIER_ACCURACY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _write_tier_accuracy(data: dict) -> None:
    """Write tier_accuracy.json atomically."""
    with _tier_acc_lock:
        try:
            write_json_safely(Path(TIER_ACCURACY_FILE), data)
        except Exception:
            logger.exception("tier_accuracy: failed to write %s", TIER_ACCURACY_FILE)


def _normalize_tier(tier: str) -> str:
    """Normalize tier strings to ELITE / HIGH / MEDIUM."""
    if not tier:
        return "MEDIUM"
    t = tier.upper()
    if t in ("ELITE", "PREMIUM"):
        return "ELITE"
    if t in ("HIGH", "STRONG"):
        return "HIGH"
    return "MEDIUM"


def update_tier_accuracy(entry: dict) -> None:
    """Update tier accuracy record from a history entry."""
    tier = _normalize_tier(entry.get("tier"))
    db = _read_tier_accuracy()
    rec = db.get(tier, {
        "tier": tier,
        "total_signals": 0,
        "winning_signals": 0,
        "losing_signals": 0,
        "win_rate_pct": 0.0,
        "avg_return_pct": 0.0,
    })
    rec["tier"] = tier
    rec["total_signals"] += 1
    if entry.get("result") == "WIN":
        rec["winning_signals"] += 1
    else:
        rec["losing_signals"] += 1
    rec["win_rate_pct"] = round(rec["winning_signals"] / rec["total_signals"] * 100, 1)
    # Recalc avg from history
    all_history = get_signal_history()
    tier_returns = [h.get("return_pct", 0) for h in all_history if _normalize_tier(h.get("tier")) == tier and h.get("return_pct") is not None]
    rec["avg_return_pct"] = round(sum(tier_returns) / len(tier_returns), 2) if tier_returns else 0.0
    db[tier] = rec
    _write_tier_accuracy(db)


def get_tier_accuracy_data() -> list:
    """Return tier accuracy records sorted ELITE > HIGH > MEDIUM."""
    db = _read_tier_accuracy()
    order = {"ELITE": 0, "HIGH": 1, "MEDIUM": 2}
    return sorted(db.values(), key=lambda x: order.get(x.get("tier", ""), 99))


def rebuild_tier_accuracy() -> dict:
    """I-01: Rebuild tier_accuracy.json from signal_history.json (source of truth)."""
    db = {}
    history = get_signal_history()
    for entry in history:
        tier = _normalize_tier(entry.get("tier"))
        rec = db.get(tier, {
            "tier": tier,
            "total_signals": 0,
            "winning_signals": 0,
            "losing_signals": 0,
            "win_rate_pct": 0.0,
            "avg_return_pct": 0.0,
        })
        rec["total_signals"] += 1
        if entry.get("result") == "WIN":
            rec["winning_signals"] += 1
        else:
            rec["losing_signals"] += 1
        db[tier] = rec
    # Recalculate derived fields
    for tier, rec in db.items():
        tier_returns = [h.get("return_pct", 0) for h in history if _normalize_tier(h.get("tier")) == tier and h.get("return_pct") is not None]
        rec["avg_return_pct"] = round(sum(tier_returns) / len(tier_returns), 2) if tier_returns else 0.0
        rec["win_rate_pct"] = round(rec["winning_signals"] / rec["total_signals"] * 100, 1)
    _write_tier_accuracy(db)
    return db


def get_tier_accuracy_stats() -> dict:
    """Return aggregate stats across all tiers."""
    data = get_tier_accuracy_data()
    total = sum(d["total_signals"] for d in data)
    wins = sum(d["winning_signals"] for d in data)
    return {
        "tiers_tracked": len(data),
        "total_signals": total,
        "winning_signals": wins,
        "losing_signals": total - wins,
        "win_rate_pct": round(wins / total * 100, 1) if total else 0.0,
    }
