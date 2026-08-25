"""
MTB Scanner API v2.0 — Railway-compatible FastAPI service.

Wraps CryptoScanner_MTB scanner v1 logic and exposes it over HTTP.
The scanner runs as an asyncio background task; all endpoints are read-only
projections of in-memory state — zero blocking I/O on the request path.

Endpoints
─────────
  GET    /health                               Liveness probe
  GET    /api/v1/scanner/signals?strategy=MTB  Latest MTB signals (list, newest first)
  GET    /api/v1/scanner/market-state          Aggregate market state across all coins
  GET    /api/v1/scanner/performance           Win-rate / return stats from tracker
  GET    /api/v1/scanner/recent?limit=N        N most-recently logged signals
  GET    /api/v1/scanner/storage               Data-directory filesystem status
  GET    /api/v1/scanner/coins                 Per-coin history depth + readiness flags
  GET    /api/v1/scanner/watchlist             Current watchlist
  POST   /api/v1/scanner/watchlist             Add a coin  {"coin": "BTC"}
  DELETE /api/v1/scanner/watchlist/{coin}      Remove a coin
  GET    /api/v1/scanner/status                Runtime telemetry snapshot
  GET    /api/v1/scanner/metrics               Aggregated signal counts + win-rate

Production features
───────────────────
  • Startup self-test  — storage, scanner, loop, and routes verified at boot
  • Global exception handlers — StarletteHTTPException / RequestValidationError / Exception
  • Hourly atomic backups  → data/backups/{signals,stats,watchlist}_backup.json
  • Graceful shutdown      — saves all files + cancels background tasks on SIGTERM

Deployment
──────────
  Binds to 0.0.0.0:PORT (Railway sets PORT automatically).
  No ngrok, no localhost hard-coding, no Telegram, no Google Drive.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

import uvicorn
from fastapi import APIRouter, FastAPI, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .scanner import (
    Scanner,
    Signal,
    SignalPerformanceTracker,
    WatchlistStore,
    detect_market_state,
    smart_filter,
    learning_filter,
    historical_filter,
    save_scanner_state,
    # storage paths & readiness thresholds (read-only, no logic change)
    STORAGE_DIR,
    SIGNAL_LOG_FILE,
    LIVE_SIGNALS_FILE,
    STATS_FILE,
    SIGNAL_HISTORY_FILE,
    COIN_PERFORMANCE_FILE,
    TIER_ACCURACY_FILE,
    SETTINGS_FILE,
    # BUG-28: use the canonical gate constants so /coins flags always
    # match the thresholds enforced by analyze_coin and phase5_score.
    ANALYZE_MIN_HISTORY,
    PHASE5_MIN_HISTORY,
    MTF_1H_WINDOW,
    # BUG-25/26/30: centralized coin-symbol validation
    validate_coin_symbol,
    _check_candles_connectivity,
    # Pair-selection engine
    resolve_coin_pair,
)
from . import watchlist_ops as _watchlist_ops

logger = logging.getLogger("scanner_api")

# =============================================================================
# SHARED STATE  (written by scanner background task, read by endpoints)
# =============================================================================

LATEST_SCANNER_SIGNALS: list[dict] = []
LATEST_MTB_SIGNALS = LATEST_SCANNER_SIGNALS  # deprecated alias, remove in V2
LATEST_MARKET_STATE: dict = {
    "market_state": "unknown",
    "timestamp":    datetime.now(timezone.utc).isoformat(),
}

# Promoted to module-level so /performance and /recent can read it.
# Set to a real instance by _scanner_loop() at startup; endpoints guard
# against None so they never crash before the loop has initialised.
_TRACKER: Optional[SignalPerformanceTracker] = None

# Exposed for /coins — reads scanner.price_history in-memory only.
_SCANNER: Optional[Scanner] = None

# Runtime telemetry — written by _scanner_loop(), read by /status
_SERVICE_START:           datetime       = datetime.now(timezone.utc)
_LAST_SCAN_TIME:          Optional[str]  = None
_SCAN_CYCLES:             int            = 0
_SIGNALS_GENERATED:       int            = 0
_LAST_DISCOVERY_SCAN:     float          = 0.0   # epoch seconds; 0 forces discovery on first cycle

# Persistence helpers  [P2-SCN-V2.7C/D]
BACKUP_DIR = os.path.join(STORAGE_DIR, "backups")

# I-04: Tier-based signal TTL (hours). MEDIUM=24h, HIGH=72h, ELITE=168h.
_TIER_TTL_HOURS: dict = {
    "ELITE":   168,   # 7 days
    "Premium": 168,
    "PREMIUM": 168,
    "High":    72,    # 3 days
    "HIGH":    72,
    "Strong":  72,
    "Medium":  24,    # 1 day
    "MEDIUM":  24,
    "Low":     24,
    "low":     24,
}
_DEFAULT_TTL_HOURS: int = 24   # fallback for unrecognised tiers

# I-04: Cleanup engine — runs every 10 minutes.
_CLEANUP_INTERVAL_SECONDS: int = int(os.getenv("CLEANUP_INTERVAL_SECONDS", "600"))


def _signal_is_alive(sig: dict, now: "datetime") -> bool:
    """Return True if sig is within its tier-based TTL window."""
    tier  = sig.get("tier", "")
    ttl_h = _TIER_TTL_HOURS.get(tier, _DEFAULT_TTL_HOURS)
    cutoff = now - timedelta(hours=ttl_h)
    try:
        ts = datetime.fromisoformat(sig.get("timestamp", ""))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts >= cutoff
    except Exception:
        return False   # malformed timestamp — treat as expired


def _merge_live_signals(fresh: list[dict]) -> list[dict]:
    """Merge this scan's fresh signals into the persisted live_signals store.

    Strategy:
    - Load the current live_signals.json (if it exists).
    - Keep only entries still within their tier TTL (I-04: MEDIUM=24h,
      HIGH=72h, ELITE=168h).
    - For any coin in `fresh`, replace the stored entry (newest scan wins).
    - Coins not in `fresh` but within TTL are retained as-is.
    """
    import json as _json

    stored: list[dict] = []
    try:
        with open(LIVE_SIGNALS_FILE, "r", encoding="utf-8") as _f:
            stored = _json.load(_f).get("signals", [])
    except Exception:
        pass  # file missing or corrupt — start fresh

    now = datetime.now(timezone.utc)

    # Keep only non-expired stored signals
    merged: dict[str, dict] = {
        sig["coin"]: sig
        for sig in stored
        if _signal_is_alive(sig, now)
    }

    # Overlay fresh signals (current scan always overwrites same coin)
    for sig in fresh:
        merged[sig["coin"]] = sig

    return list(merged.values())


_SCANNER_TASK: Optional[asyncio.Task] = None   # scanner background loop
_BACKUP_TASK:  Optional[asyncio.Task] = None   # hourly backup loop
_CLEANUP_TASK: Optional[asyncio.Task] = None   # 10-min expiry cleanup loop

# I-04: Cleanup engine stats — written by _run_cleanup(), read by dashboard.
_CLEANUP_STATS: dict = {
    "last_cleanup_time":      None,   # ISO string or None
    "next_cleanup_time":      None,   # ISO string or None
    "expired_last_run":       0,      # signals removed in last cleanup
    "active_count":           0,      # signals remaining after last cleanup
    "total_expired_lifetime": 0,      # cumulative lifetime expired count
}

# I-05: Scanner health monitor stats
_HEALTH_STATS: dict = {
    "api_status":             "ONLINE",     # ONLINE / OFFLINE
    "last_successful_scan":   None,         # ISO string
    "total_scans":            0,
    "failed_scans":           0,
    "consecutive_failures":   0,
    "scan_duration_ms":       0,
    "current_market_status":  "",
    "health_score":           100,
    "health_color":           "green",      # green / yellow / red
}

# I-07: Restart recovery stats
_RECOVERY_STATS: dict = {
    "last_restart_time":   _SERVICE_START.isoformat(),
    "recovered_signals":   0,
    "recovery_status":     "SUCCESS",
}

# SP1.1: Bootstrap status — written by _scanner_loop, read by /status
_BOOTSTRAP_STATUS: dict = {
    "state":            "pending",   # pending | running | complete | failed | disabled
    "started_at":       None,        # ISO string
    "completed_at":     None,        # ISO string
    "duration_s":       None,        # float
    "coins_attempted":  0,
    "coins_loaded":     0,
    "coins_failed":     0,
    "coins_skipped":    0,
    "avg_history_len":  0.0,
    "min_history_len":  0,
    "ema_ready":        False,
    "mtf_ready":        False,
    "phase5_ready":     False,
    "failed_coins":     [],
}

# I-08: Manual refresh event — triggers immediate scan
_REFRESH_EVENT: asyncio.Event = asyncio.Event()

# =============================================================================
# FASTAPI APP
# =============================================================================

async def startup_event() -> None:
    """Single startup path for the scanner subsystem.

    Runs the startup self-test (which also creates the scanner background
    loop) and launches the backup + cleanup loops.  Idempotent: callers
    that have already initialised the loops will not re-create them.

    Called by:
      - this module's own _lifespan (when scanner runs standalone)
      - the dashboard's lifespan in app.py (when embedded in PROJECT-ALPHA)
    """
    global _BACKUP_TASK, _CLEANUP_TASK
    await _run_startup_selftest()
    if _BACKUP_TASK is None or _BACKUP_TASK.done():
        _BACKUP_TASK = asyncio.create_task(_backup_loop())
        logger.info("Backup loop started (interval=3600s)")
    if _CLEANUP_TASK is None or _CLEANUP_TASK.done():
        _CLEANUP_TASK = asyncio.create_task(_cleanup_loop())
        logger.info("Cleanup loop started (interval=%ds)", _CLEANUP_INTERVAL_SECONDS)
    logger.info("Scanner background task created")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """FastAPI lifespan: replaces deprecated @app.on_event startup/shutdown."""
    await startup_event()
    yield
    await _do_shutdown_save()


app = FastAPI(title="MTB Scanner API", version="2.0", lifespan=_lifespan)

# All scanner HTTP routes are registered on this router so that the dashboard
# app.py can include them directly without running a second FastAPI process.
scanner_router = APIRouter()


# ---------------------------------------------------------------------------
# 1. Health endpoint
# ---------------------------------------------------------------------------

@scanner_router.get("/health", dependencies=[])
async def health():
    return {
        "status":  "healthy",
        "service": "scanner_v2",
        "version": "2.0",
    }


# ---------------------------------------------------------------------------
# 2. MTB Signals endpoint
# ---------------------------------------------------------------------------

_MTB_PRIORITIES = {"Elite", "High", "Medium"}

@scanner_router.get("/api/v1/scanner/signals", dependencies=[])
async def scanner_signals(strategy: str = Query(default="MTB")):
    """
    Returns MTB-ready signals filtered to Elite / High / Medium priority only.
    Reads from tracker recent signals (same source as /recent).
    Newest first.
    """

    # BUG-27: strip before normalizing so callers using "mtb" or " MTB " are
    # treated the same as "MTB" — previously only exact-case "MTB" matched.
    if strategy.strip().upper() != "MTB":
        return JSONResponse(content=[])

    try:
        tracker = _TRACKER

        if tracker is None:
            return JSONResponse(content=[])

        recent = tracker.recent_signals(limit=100)

        filtered = [
            {
                "coin":             s.get("coin", ""),
                "market_state":     s.get("market_state", ""),
                "opportunity_type": s.get("opportunity_type", ""),
                "priority":         s.get("priority", ""),
                "score":            s.get("opportunity_score", 0),
                "confidence":       s.get("opp_confidence", 0),
                "risk":             s.get("risk_level", ""),
                "timestamp":        s.get("timestamp", ""),
            }
            for s in recent
            if s.get("priority") in _MTB_PRIORITIES
        ]

        filtered.sort(
            key=lambda x: x["timestamp"],
            reverse=True
        )

        return JSONResponse(content=filtered)

    except Exception:
        logger.exception("/signals: unexpected error")
        return JSONResponse(content=[])


# ---------------------------------------------------------------------------
# 3. Market State endpoint
# ---------------------------------------------------------------------------

@scanner_router.get("/api/v1/scanner/market-state")
async def market_state():
    """
    Returns the current aggregate market state across all tracked coins.
    Always returns a dict — never None.

    BUG-29: wrapped in try/except matching every other endpoint's "HTTP 200
    always" contract. LATEST_MARKET_STATE is normally a plain dict written
    by _scanner_loop, but if it were ever to contain a non-JSON-serialisable
    value, JSONResponse would raise and this previously had no fallback.
    """
    try:
        return JSONResponse(content=LATEST_MARKET_STATE)
    except Exception:
        logger.exception("/market-state: unexpected error — returning safe default")
        return JSONResponse(content={
            "market_state": "unknown",
            "timestamp":    datetime.now(timezone.utc).isoformat(),
        })


# ---------------------------------------------------------------------------
# 4. Performance endpoint  [P2-SCN-V2.2]
# ---------------------------------------------------------------------------

@scanner_router.get("/api/v1/scanner/performance")
async def scanner_performance():
    """
    Returns win-rate and return statistics computed from the signal log.
    Reads from the SignalPerformanceTracker only — no live scanning.
    Always returns HTTP 200 with safe defaults on empty / uninitialized data.
    """
    _SAFE: dict = {
        "status":      "success",
        "model":       "v12.2",
        "signals_total":     0,
        "signals_evaluated": 0,
        "win_rate":          0.0,
        "avg_returns":       {"1h": None, "4h": None, "24h": None},
        "best_coin":              None,
        "best_coin_return_24h":   None,
        "market_state_distribution": {
            "breakout": 0, "bull_trend": 0, "recovery": 0,
            "pullback": 0, "sideways":  0, "downtrend": 0,
        },
        "priority_distribution": {
            "Elite": 0, "High": 0, "Medium": 0, "Watch": 0, "Ignore": 0,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    try:
        tracker = _TRACKER
        if tracker is None:
            return JSONResponse(content=_SAFE)

        raw_signals: list[dict] = tracker._data.get("signals", [])

        # ── totals ──────────────────────────────────────────────────────────
        signals_total = len(raw_signals)

        # ── evaluation buckets ──────────────────────────────────────────────
        horizons = ("1h", "4h", "24h")
        returns_by_horizon: dict[str, list[float]] = {h: [] for h in horizons}
        evaluated_set: set[int] = set()     # indices of signals with ≥1 eval

        best_coin: Optional[str]        = None
        best_return_24h: Optional[float] = None

        for idx, item in enumerate(raw_signals):
            evals: dict = item.get("evaluations") or {}
            for h in horizons:
                ev = evals.get(h)
                if ev:
                    try:
                        pct = float(ev["change_percent"])
                        returns_by_horizon[h].append(pct)
                        evaluated_set.add(idx)
                    except (KeyError, TypeError, ValueError):
                        pass
            # best_coin by 24h return
            ev24 = evals.get("24h")
            if ev24:
                try:
                    r24 = float(ev24["change_percent"])
                    if best_return_24h is None or r24 > best_return_24h:
                        best_return_24h = r24
                        best_coin = item.get("coin")
                except (KeyError, TypeError, ValueError):
                    pass

        signals_evaluated = len(evaluated_set)

        # win_rate = % of evaluated signals where latest return > 0
        wins = 0
        for idx in evaluated_set:
            item  = raw_signals[idx]
            evals = item.get("evaluations") or {}
            for h in ("24h", "4h", "1h"):
                ev = evals.get(h)
                if ev:
                    try:
                        if float(ev["change_percent"]) > 0:
                            wins += 1
                    except (KeyError, TypeError, ValueError):
                        pass
                    break   # use the longest available horizon for win/loss

        win_rate = round(wins / signals_evaluated * 100, 2) if signals_evaluated else 0.0

        def _safe_avg(vals: list[float]) -> Optional[float]:
            if not vals:
                return None
            return round(sum(vals) / len(vals), 4)

        avg_returns = {h: _safe_avg(returns_by_horizon[h]) for h in horizons}

        # ── distributions ────────────────────────────────────────────────────
        ms_dist: dict[str, int] = {
            "breakout": 0, "bull_trend": 0, "recovery": 0,
            "pullback": 0, "sideways":  0, "downtrend": 0,
        }
        pri_dist: dict[str, int] = {
            "Elite": 0, "High": 0, "Medium": 0, "Watch": 0, "Ignore": 0,
        }

        for item in raw_signals:
            ms  = item.get("market_state", "")
            pri = item.get("priority",     "")
            if ms  in ms_dist:  ms_dist[ms]   += 1
            if pri in pri_dist: pri_dist[pri] += 1

        return JSONResponse(content={
            "status":      "success",
            "model":       "v12.2",
            "signals_total":     signals_total,
            "signals_evaluated": signals_evaluated,
            "win_rate":          win_rate,
            "avg_returns":       avg_returns,
            "best_coin":              best_coin,
            "best_coin_return_24h":   best_return_24h,
            "market_state_distribution": ms_dist,
            "priority_distribution":     pri_dist,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    except Exception:
        logger.exception("/performance: unexpected error — returning safe defaults")
        _SAFE["timestamp"] = datetime.now(timezone.utc).isoformat()
        return JSONResponse(content=_SAFE)


# ---------------------------------------------------------------------------
# 5. Recent signals endpoint  [P2-SCN-V2.2]
# ---------------------------------------------------------------------------

@scanner_router.get("/api/v1/scanner/recent")
async def scanner_recent(limit: int = Query(default=10, ge=1, le=200)):
    """
    Returns the most recently logged signals from the performance tracker.
    Newest first.  Reads stored data — no live scanning.
    Always returns a list; returns [] on empty / uninitialized data.
    """
    try:
        tracker = _TRACKER
        if tracker is None:
            return JSONResponse(content=[])

        recent = tracker.recent_signals(limit=limit)
        result = []
        for item in recent:
            result.append({
                "coin":             item.get("coin",             ""),
                "market_state":     item.get("market_state",     ""),
                "opportunity_type": item.get("opportunity_type", ""),
                "priority":         item.get("priority",         ""),
                "score":            item.get("opportunity_score", 0),
                "confidence":       item.get("opp_confidence",    0),
                "timestamp":        item.get("timestamp",         ""),
            })
        return JSONResponse(content=result)

    except Exception:
        logger.exception("/recent: unexpected error — returning []")
        return JSONResponse(content=[])


# ---------------------------------------------------------------------------
# 6. Storage endpoint  [P2-SCN-V2.3]
# ---------------------------------------------------------------------------

@scanner_router.get("/api/v1/scanner/storage")
async def scanner_storage():
    """
    Returns filesystem status for the scanner's data directory.
    Reads existing files only — no scanning, no HTTP calls.
    Always returns HTTP 200; missing files are reported as False / 0 / null.
    """
    try:
        from pathlib import Path

        signals_path = Path(SIGNAL_LOG_FILE)
        stats_path   = Path(STATS_FILE)
        # price history lives in-memory; we report whether the data dir exists
        history_path = Path(STORAGE_DIR)

        signals_exists = signals_path.is_file()
        stats_exists   = stats_path.is_file()
        history_exists = history_path.is_dir()

        # signals count from tracker if available, else parse file directly
        signals_count = 0
        tracker = _TRACKER
        if tracker is not None:
            signals_count = len(tracker._data.get("signals", []))
        elif signals_exists:
            try:
                import json as _json
                def _read_signals_count():
                    return _json.loads(signals_path.read_text(encoding="utf-8"))
                logger.debug("[scanner] offloading signals file read (storage) to thread")
                data = await asyncio.to_thread(_read_signals_count)
                signals_count = len(data.get("signals", []))
            except Exception:
                signals_count = 0

        # last_updated = mtime of signals file
        last_updated: Optional[str] = None
        if signals_exists:
            try:
                mtime = signals_path.stat().st_mtime
                last_updated = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
            except Exception:
                pass

        return JSONResponse(content={
            "signals_file_exists": signals_exists,
            "stats_file_exists":   stats_exists,
            "history_file_exists": history_exists,
            "signals_count":       signals_count,
            "storage_path":        str(STORAGE_DIR),
            "last_updated":        last_updated,
        })

    except Exception:
        logger.exception("/storage: unexpected error — returning safe defaults")
        return JSONResponse(content={
            "signals_file_exists": False,
            "stats_file_exists":   False,
            "history_file_exists": False,
            "signals_count":       0,
            "storage_path":        str(STORAGE_DIR),
            "last_updated":        None,
        })


# ---------------------------------------------------------------------------
# 7. Coins endpoint  [P2-SCN-V2.3]
# ---------------------------------------------------------------------------

@scanner_router.get("/api/v1/scanner/coins")
async def scanner_coins():
    """
    Returns per-coin history depth and readiness flags from the scanner's
    in-memory price_history dict.  No live rescanning, no HTTP calls.
    Always returns a list; returns [] when the scanner is not yet running.
    """
    try:
        sc = _SCANNER
        if sc is None:
            return JSONResponse(content=[])

        result = []
        for coin, history in sc.price_history.items():
            n = len(history)
            # BUG-28: flags now derived from the same constants used by
            # analyze_coin() and phase5_score() so they cannot drift apart.
            analyze_ready = n >= ANALYZE_MIN_HISTORY   # 22 — analyze_coin gate
            phase5_ready  = n >= PHASE5_MIN_HISTORY    # 21 — phase5_score gate
            mtf_ready     = n >= MTF_1H_WINDOW         # 48 — full 1h window

            try:
                ms: Optional[str] = detect_market_state(history) if n >= 6 else None
            except Exception:
                ms = None

            result.append({
                "coin":          coin,
                "history_len":   n,
                "analyze_ready": analyze_ready,
                "phase5_ready":  phase5_ready,
                "mtf_ready":     mtf_ready,
                "market_state":  ms,
            })

        # sort: longest history first so callers see the most data-rich coins up top
        result.sort(key=lambda x: x["history_len"], reverse=True)
        return JSONResponse(content=result)

    except Exception:
        logger.exception("/coins: unexpected error — returning []")
        return JSONResponse(content=[])


# ---------------------------------------------------------------------------
# 8. Watchlist GET  [P2-SCN-V2.5]
# ---------------------------------------------------------------------------

@scanner_router.get("/api/v1/scanner/watchlist")
async def watchlist_get():
    """Return the current watchlist. HTTP 200 always."""
    try:
        sc = _SCANNER
        if sc is None:
            # scanner not yet started — load directly from file
            store = WatchlistStore()
            coins = store.all()
        else:
            coins = sc.watchlist_store.all()
        return JSONResponse(content={"count": len(coins), "coins": coins})
    except Exception:
        logger.exception("/watchlist GET: unexpected error")
        return JSONResponse(content={"count": 0, "coins": []})


# ---------------------------------------------------------------------------
# 9. Watchlist POST  [P2-SCN-V2.5]
# ---------------------------------------------------------------------------

class _AddCoinBody(BaseModel):
    coin: str


@scanner_router.post("/api/v1/scanner/watchlist")
async def watchlist_add(body: _AddCoinBody):
    """
    Add a coin to the watchlist.

    Delegates to watchlist_ops.add_coin() — the single canonical
    implementation shared with /api/watchlist/add in app.py.
    Validates the symbol, resolves the best trading pair, and writes
    to WatchlistStore.  HTTP 200 always.
    """
    try:
        result = await _watchlist_ops.add_coin(body.coin)

        if not result["ok"]:
            return JSONResponse(content={
                "status": "rejected",
                "reason": result["reason"],
                "coin":   result["coin"],
            })

        if result["already_existed"]:
            return JSONResponse(content={
                "status": "already_exists",
                "coin":   result["coin"],
                "count":  len(result["watchlist"]),
            })

        return JSONResponse(content={
            "status": "success",
            "coin":   result["coin"],
            "count":  len(result["watchlist"]),
        })
    except Exception:
        logger.exception("/watchlist POST: unexpected error")
        return JSONResponse(content={"status": "error", "coin": "", "count": 0})


# ---------------------------------------------------------------------------
# 10. Watchlist DELETE  [P2-SCN-V2.5]
# ---------------------------------------------------------------------------

@scanner_router.delete("/api/v1/scanner/watchlist/{coin}")
async def watchlist_remove(
    coin: str = Path(..., description="Coin symbol to remove, e.g. BTC"),
):
    """
    Remove a coin from the watchlist.
    If the coin is not on the list the call still returns success — idempotent.

    Delegates to watchlist_ops.remove_coin() — the single canonical
    implementation shared with /api/watchlist/remove in app.py.
    HTTP 200 always.
    """
    try:
        result = await _watchlist_ops.remove_coin(coin)
        return JSONResponse(content={
            "status":  "success",
            "removed": result["coin"],
            "count":   len(result["watchlist"]),
        })
    except Exception:
        logger.exception("/watchlist DELETE: unexpected error")
        return JSONResponse(content={"status": "error", "removed": "", "count": 0})


# ---------------------------------------------------------------------------
# 11a. Pair Resolution Engine endpoint
# ---------------------------------------------------------------------------

@scanner_router.get("/api/v1/scanner/resolve-pair/{coin}")
async def resolve_pair(
    coin: str = Path(..., description="Coin symbol to resolve, e.g. BTC, PEPE"),
):
    """
    Resolve a coin symbol to its best available trading pair.

    Priority: INR > USDT > rejected (no pair found).
    Uses the live scanner ticker cache — no additional API calls are made.

    Returns resolved pair info:
      - resolved=true  → pair and quote are populated
      - resolved=false → coin does not exist in any supported quote market

    HTTP 200 always.
    """
    try:
        is_valid, symbol, _ = validate_coin_symbol(coin)
        if not is_valid:
            return JSONResponse(content={
                "coin":     coin.strip().upper(),
                "pair":     None,
                "quote":    None,
                "resolved": False,
                "reason":   "invalid_symbol",
            })

        tickers: list | None = None
        sc = _SCANNER
        if sc is not None:
            async with sc._ticker_lock:
                if sc._ticker_cache:
                    tickers = list(sc._ticker_cache)

        result = resolve_coin_pair(symbol, tickers=tickers)
        return JSONResponse(content=result)
    except Exception:
        logger.exception("/resolve-pair GET: unexpected error")
        return JSONResponse(content={
            "coin":     coin,
            "pair":     None,
            "quote":    None,
            "resolved": False,
            "reason":   "error",
        })


# ---------------------------------------------------------------------------
# 11. Status endpoint  [P2-SCN-V2.6]
# ---------------------------------------------------------------------------

@scanner_router.get("/api/v1/scanner/status")
async def scanner_status():
    """
    Runtime health snapshot — reads in-memory state only, no scanning.
    HTTP 200 always.
    """
    try:
        from pathlib import Path as _Path
        uptime = int((datetime.now(timezone.utc) - _SERVICE_START).total_seconds())
        sc      = _SCANNER
        running = sc is not None

        watchlist_size = 0
        if sc is not None:
            try:
                watchlist_size = len(sc.watchlist_store.all())
            except Exception:
                pass

        storage_ready = _Path(STORAGE_DIR).is_dir()

        return JSONResponse(content={
            "service":           "scanner_v2",
            "version":           "2.0",
            "running":           running,
            "uptime_seconds":    uptime,
            "last_scan_time":    _LAST_SCAN_TIME,
            "scan_cycles":       _SCAN_CYCLES,
            "signals_generated": _SIGNALS_GENERATED,
            "watchlist_size":    watchlist_size,
            "memory_signals":    len(LATEST_MTB_SIGNALS),
            "storage_ready":     storage_ready,
            "railway":           True,
            # I-05 / I-07: attach health and recovery stats
            "health":            _HEALTH_STATS,
            "recovery":          _RECOVERY_STATS,
            # SP1.1: bootstrap status
            "bootstrap":         _BOOTSTRAP_STATUS,
        })
    except Exception:
        logger.exception("/status: unexpected error — returning safe defaults")
        return JSONResponse(content={
            "service":           "scanner_v2",
            "version":           "2.0",
            "running":           False,
            "uptime_seconds":    0,
            "last_scan_time":    None,
            "scan_cycles":       0,
            "signals_generated": 0,
            "watchlist_size":    0,
            "memory_signals":    0,
            "storage_ready":     False,
            "railway":           True,
            "health":            {},
            "recovery":          {},
            "bootstrap":         _BOOTSTRAP_STATUS,
        })


# ---------------------------------------------------------------------------
# I-05: Dedicated health endpoint
# ---------------------------------------------------------------------------

@scanner_router.get("/api/v1/scanner/health")
async def scanner_health():
    """Return scanner health monitor metrics.
    HTTP 200 always — even if the scanner is offline.
    """
    return JSONResponse(content={
        "status":             "ok",
        "health":             _HEALTH_STATS,
        "recovery":           _RECOVERY_STATS,
        "service":            "scanner_v2",
        "version":            "2.0",
        "timestamp":          datetime.now(timezone.utc).isoformat(),
    })


# ---------------------------------------------------------------------------
# I-08: Manual refresh endpoint
# ---------------------------------------------------------------------------

@scanner_router.post("/api/v1/scanner/refresh")
async def scanner_refresh():
    """Trigger an immediate scan cycle.
    Sets the refresh event so the scanner loop breaks out of sleep immediately.
    Returns immediately — scan is async.
    """
    _REFRESH_EVENT.set()
    logger.info("Manual refresh triggered via API")
    return JSONResponse(content={
        "status":    "ok",
        "message":   "Scan triggered",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


# ---------------------------------------------------------------------------
# 12. Metrics endpoint  [P2-SCN-V2.6]
# ---------------------------------------------------------------------------

@scanner_router.get("/api/v1/scanner/metrics")
async def scanner_metrics():
    """
    Aggregated signal metrics from the tracker — no calculations beyond
    what the tracker already holds.  HTTP 200 always.
    """
    _SAFE = {
        "signals_total": 0,
        "elite":  0, "high": 0, "medium": 0,
        "market_states": {
            "breakout": 0, "bull_trend": 0, "recovery": 0,
            "pullback": 0, "sideways":  0, "downtrend": 0,
        },
        "avg_returns": {"1h": None, "4h": None, "24h": None},
        "win_rate": 0.0,
    }

    try:
        tracker = _TRACKER
        if tracker is None:
            return JSONResponse(content=_SAFE)

        raw: list[dict] = tracker._data.get("signals", [])

        # priority counts
        pri_counts = {"Elite": 0, "High": 0, "Medium": 0}
        ms_counts  = {k: 0 for k in _SAFE["market_states"]}

        horizons = ("1h", "4h", "24h")
        ret_buckets: dict[str, list[float]] = {h: [] for h in horizons}
        wins = 0; evaluated = 0

        for item in raw:
            pri = item.get("priority", "")
            if pri in pri_counts:
                pri_counts[pri] += 1

            ms = item.get("market_state", "")
            if ms in ms_counts:
                ms_counts[ms] += 1

            evals = item.get("evaluations") or {}
            has_eval = False
            for h in horizons:
                ev = evals.get(h)
                if ev:
                    try:
                        pct = float(ev["change_percent"])
                        ret_buckets[h].append(pct)
                        has_eval = True
                    except (KeyError, TypeError, ValueError):
                        pass
            if has_eval:
                evaluated += 1
                # win = positive return on the longest evaluated horizon
                for h in ("24h", "4h", "1h"):
                    ev = evals.get(h)
                    if ev:
                        try:
                            if float(ev["change_percent"]) > 0:
                                wins += 1
                        except (KeyError, TypeError, ValueError):
                            pass
                        break

        def _avg(vals: list) -> Optional[float]:
            return round(sum(vals) / len(vals), 4) if vals else None

        win_rate = round(wins / evaluated * 100, 2) if evaluated else 0.0

        return JSONResponse(content={
            "signals_total": len(raw),
            "elite":  pri_counts["Elite"],
            "high":   pri_counts["High"],
            "medium": pri_counts["Medium"],
            "market_states": ms_counts,
            "avg_returns":   {h: _avg(ret_buckets[h]) for h in horizons},
            "win_rate":      win_rate,
        })

    except Exception:
        logger.exception("/metrics: unexpected error — returning safe defaults")
        return JSONResponse(content=_SAFE)


# =============================================================================
# SCANNER BACKGROUND TASK
# =============================================================================

async def _no_op_alert(signal: Signal, source: str) -> None:
    """No-op alert callback — signals are served via the HTTP API instead."""
    pass


async def _scanner_loop() -> None:
    """
    Runs the Scanner indefinitely, refreshing LATEST_MTB_SIGNALS and
    LATEST_MARKET_STATE after every scan cycle.
    """
    global LATEST_SCANNER_SIGNALS, LATEST_MTB_SIGNALS, LATEST_MARKET_STATE, \
           _TRACKER, _SCANNER, _LAST_SCAN_TIME, _SCAN_CYCLES, _SIGNALS_GENERATED, \
           _LAST_DISCOVERY_SCAN, _BOOTSTRAP_STATUS
    logger.info("ENTERED _scanner_loop")

    # ── Pre-load persisted signals so dashboard is non-empty immediately ──────
    # This runs before the first scan cycle completes (~5 min), so the dashboard
    # shows the last known signals from the previous session right after startup.
    try:
        import json as _json
        def _read_live_signals():
            with open(LIVE_SIGNALS_FILE, "r", encoding="utf-8") as _f:
                return _json.load(_f).get("signals", [])
        _pre = await asyncio.to_thread(_read_live_signals)
        if _pre:
            LATEST_SCANNER_SIGNALS = _pre
            LATEST_MTB_SIGNALS = LATEST_SCANNER_SIGNALS
            logger.info(
                "Pre-loaded %d persisted signals from live_signals.json", len(_pre)
            )
    except FileNotFoundError:
        pass  # NF-5: first ever run — live_signals.json does not exist yet, no-op
    except Exception:
        # NF-5: file exists but is unreadable/corrupt, or unexpected I/O error.
        # Dashboard will start with an empty signal list; next scan cycle repopulates it.
        logger.exception("Startup signal preload failed — starting with empty signal list")

    watchlist  = WatchlistStore()
    tracker    = SignalPerformanceTracker()
    _TRACKER   = tracker          # expose to /performance and /recent endpoints
    scanner    = Scanner(
        watchlist_store=watchlist,
        alert_callback=_no_op_alert,
        performance_tracker=tracker,
    )
    _SCANNER = scanner            # expose to /coins endpoint

    logger.info("MTB Scanner API: starting bootstrap...")
    try:
        # SP1.1: mark bootstrap as running before starting
        _BOOTSTRAP_STATUS["state"]      = "running"
        _BOOTSTRAP_STATUS["started_at"] = datetime.now(timezone.utc).isoformat()
        logger.info("[Bootstrap] state=running")

        if not _check_candles_connectivity():
            logger.critical(
                "BOOTSTRAP SKIPPED: public.coindcx.com is unreachable. "
                "Set COINDCX_CANDLES_URL env var to override, or check "
                "Railway network egress. Scanner will run without "
                "historical data — 0 signals until resolved."
            )
            _BOOTSTRAP_STATUS["state"] = "skipped"
            _BOOTSTRAP_STATUS["completed_at"] = datetime.now(timezone.utc).isoformat()
        else:
            result = await scanner.run_bootstrap()

            # SP1.1: populate status from BootstrapResult
            _BOOTSTRAP_STATUS.update({
                "state":           "complete",
                "completed_at":    datetime.now(timezone.utc).isoformat(),
                "duration_s":      result.duration_s,
                "coins_attempted": result.coins_attempted,
                "coins_loaded":    result.coins_loaded,
                "coins_failed":    result.coins_failed,
                "coins_skipped":   result.coins_skipped,
                "avg_history_len": result.avg_history_len,
                "min_history_len": result.min_history_len,
                "ema_ready":       result.ema_ready,
                "mtf_ready":       result.mtf_ready,
                "phase5_ready":    result.phase5_ready,
                "failed_coins":    result.failed_coins,
            })
            logger.info(
                "[Bootstrap] state=complete loaded=%d failed=%d skipped=%d duration=%.1fs",
                result.coins_loaded, result.coins_failed, result.coins_skipped, result.duration_s,
            )
    except Exception:
        _BOOTSTRAP_STATUS["state"]        = "failed"
        _BOOTSTRAP_STATUS["completed_at"] = datetime.now(timezone.utc).isoformat()
        logger.exception(
            "[Bootstrap] state=failed — continuing without pre-loaded history"
        )

    # I-07: Verify recovery — count how many signals were pre-loaded
    recovered = len(LATEST_MTB_SIGNALS)
    _RECOVERY_STATS["recovered_signals"] = recovered
    logger.info("Recovery: %d signals pre-loaded", recovered)

    # First scan immediately after bootstrap
    logger.info("MTB Scanner API: starting scan loop")
    while True:
        scan_start_ms = asyncio.get_running_loop().time() * 1000
        try:
            from . import monitoring_metrics as _mm
            _mm.log_event("Scan started")
        except Exception:
            pass
        try:
            # I-05: Retry get_tickers up to 5 times with 10s delay
            tickers: list = []
            for attempt in range(1, 6):
                try:
                    tickers = await scanner.get_tickers(force=True)
                    break
                except Exception as e:
                    logger.warning(
                        "Ticker fetch attempt %d/5 failed: %s",
                        attempt, str(e)
                    )
                    try:
                        from . import monitoring_metrics as _mm
                        _mm.record_api_call(success=False, retry=True)
                    except Exception:
                        pass
                    if attempt < 5:
                        await asyncio.sleep(10)
                    else:
                        raise

            logger.info("Tickers Downloaded=%d", len(tickers))
            try:
                from . import monitoring_metrics as _mm
                _mm.log_event(f"Tickers downloaded: {len(tickers)}")
            except Exception:
                pass

            scanner.evaluate_signal_performance(tickers)

            watchlist_sigs = await scanner.scan_watchlist(tickers)

            _discovery_interval = int(os.getenv("DISCOVERY_INTERVAL_SECONDS", "900"))
            _now = asyncio.get_running_loop().time()
            if _now - _LAST_DISCOVERY_SCAN >= _discovery_interval:
                discovery_sigs       = await scanner.scan_market(tickers)
                _LAST_DISCOVERY_SCAN = _now
                logger.info("Discovery scan ran — next in %ds", _discovery_interval)
            else:
                discovery_sigs = []
                logger.debug(
                    "Discovery skipped — %ds until next run",
                    int(_discovery_interval - (_now - _LAST_DISCOVERY_SCAN))
                )

            all_signals = watchlist_sigs + discovery_sigs

            logger.info(
                "Watchlist=%d Discovery=%d Total=%d",
                len(watchlist_sigs), len(discovery_sigs), len(all_signals)
            )

            # Filter and convert to API-friendly dicts
            fresh: list[dict] = []
            for sig in all_signals:
                if not smart_filter(sig):
                    continue
                if not learning_filter(sig, tracker):
                    continue
                if not historical_filter(sig):
                    continue
                fresh.append({
                    "coin":             sig.coin,
                    "market":           sig.market,
                    "market_state":     sig.market_state,
                    "opportunity_type": sig.opportunity_type,
                    "priority":         sig.priority,
                    "score":            sig.opportunity_score,
                    "confidence":       sig.opp_confidence,
                    "tier":             sig.tier,
                    "final_score":      sig.final_score,
                    "risk_level":       sig.risk_level,
                    "price":            sig.price,
                    "coin_class":       sig.coin_class,
                    "timestamp":        sig.created_at.isoformat(),
                })

            LATEST_SCANNER_SIGNALS = fresh
            LATEST_MTB_SIGNALS     = LATEST_SCANNER_SIGNALS
            logger.info(
                "LIVE SIGNALS:%d", len(LATEST_SCANNER_SIGNALS)
            )
            _SCAN_CYCLES       += 1
            _SIGNALS_GENERATED += len(fresh)
            _LAST_SCAN_TIME     = datetime.now(timezone.utc).isoformat()

            # Aggregate market state
            state = scanner.aggregate_market_state()
            LATEST_MARKET_STATE = {
                "market_state": state,
                "timestamp":    datetime.now(timezone.utc).isoformat(),
            }

            # Persist live signals
            try:
                from bots.scanner_bot.scanner import write_json_safely, Path
                merged = _merge_live_signals(fresh)
                write_json_safely(Path(LIVE_SIGNALS_FILE), {"signals": merged})
                LATEST_SCANNER_SIGNALS = merged
                LATEST_MTB_SIGNALS     = LATEST_SCANNER_SIGNALS
                logger.info(
                    "live_signals.json updated: fresh=%d merged=%d",
                    len(fresh), len(merged),
                )
            except Exception:
                logger.exception("Failed to persist live_signals.json")

            try:
                _active = LATEST_MTB_SIGNALS
                save_scanner_state(_active, LATEST_MARKET_STATE, {
                    "total_signals":  len(_active),
                    "elite_signals":  sum(1 for s in _active if s.get("tier") in ("ELITE", "Premium", "PREMIUM")),
                    "high_signals":   sum(1 for s in _active if s.get("tier") in ("High", "HIGH", "Strong")),
                    "medium_signals": sum(1 for s in _active if s.get("tier") in ("Medium", "MEDIUM", "Low", "low")),
                    "updated_at":     datetime.now(timezone.utc).isoformat(),
                })
            except Exception:
                logger.exception("Failed to persist scanner state files")

            # I-05: Health stats — SUCCESS
            scan_dur = int(asyncio.get_running_loop().time() * 1000 - scan_start_ms)
            _HEALTH_STATS["api_status"]           = "ONLINE"
            _HEALTH_STATS["last_successful_scan"] = datetime.now(timezone.utc).isoformat()
            _HEALTH_STATS["total_scans"]          = _SCAN_CYCLES
            _HEALTH_STATS["consecutive_failures"]  = 0
            _HEALTH_STATS["scan_duration_ms"]     = scan_dur
            _HEALTH_STATS["current_market_status"] = state
            _HEALTH_STATS["health_score"]         = 100
            _HEALTH_STATS["health_color"]         = "green"

            logger.info(
                "Scan done: %d signals, market_state=%s",
                len(fresh), state,
            )
            try:
                from . import monitoring_metrics as _mm
                _mm.record_cycle_complete(duration_ms=scan_dur)
                _mm.log_event(f"Scan completed: {len(fresh)} signals, market={state}")
            except Exception:
                pass

        except Exception as e:
            # I-05: Health stats — FAILURE
            _HEALTH_STATS["failed_scans"] += 1
            _HEALTH_STATS["consecutive_failures"] += 1
            fails = _HEALTH_STATS["consecutive_failures"]
            if fails >= 3:
                _HEALTH_STATS["api_status"] = "OFFLINE"
                _HEALTH_STATS["health_score"] = 0
                _HEALTH_STATS["health_color"] = "red"
            elif fails >= 1:
                _HEALTH_STATS["health_score"] = max(0, 100 - (fails * 30))
                _HEALTH_STATS["health_color"] = "yellow"
            logger.exception("Scanner loop error — retrying after interval")
            try:
                from . import monitoring_metrics as _mm
                _err_dur = asyncio.get_running_loop().time() * 1000 - scan_start_ms
                _mm.record_cycle_complete(duration_ms=_err_dur, error=str(e))
                _mm.log_event(f"Scan error: {str(e)[:150]}", level="error")
            except Exception:
                pass

        # I-08: Use refresh_event for sleep so manual refresh triggers immediately
        try:
            await asyncio.wait_for(
                _REFRESH_EVENT.wait(),
                timeout=int(os.getenv("SCAN_INTERVAL_SECONDS", "300"))
            )
        except asyncio.TimeoutError:
            pass
        finally:
            _REFRESH_EVENT.clear()


# ---------------------------------------------------------------------------
# Scanner Center V2 — additive, read-only monitoring endpoint.
# Purely observability: exposes in-memory counters collected by
# monitoring_metrics.py. Never touches scanning/signal/trading state and
# cannot affect any existing endpoint's response shape. HTTP 200 always —
# a failure here can never stop the scanner loop or any other route.
# ---------------------------------------------------------------------------

_MONITORING_SAFE_DEFAULT = {
    "health":    {},
    "funnel":    {"coins_scanned": 0, "passed_volume": 0, "passed_trend": 0,
                   "passed_score": 0, "signals_generated": 0},
    "api":       {},
    "cache":     {},
    "cycles":    {},
    "event_log": [],
}


@scanner_router.get("/api/v1/scanner/monitoring")
async def scanner_monitoring():
    """
    Scanner Center V2 — aggregated observability snapshot.
    Additive-only: does not modify or read from any existing endpoint's
    state, and its own failure is fully isolated (safe defaults returned).
    """
    try:
        from . import monitoring_metrics as _mm
        snap = _mm.snapshot()

        health = dict(_HEALTH_STATS)
        health["uptime_seconds"] = int(
            (datetime.now(timezone.utc) - _SERVICE_START).total_seconds()
        )
        health["scan_cycles"] = _SCAN_CYCLES
        health["signals_generated"] = _SIGNALS_GENERATED
        health["last_restart_time"] = _RECOVERY_STATS.get("last_restart_time")
        health["recovered_signals"] = _RECOVERY_STATS.get("recovered_signals")

        return JSONResponse(content={
            "health":    health,
            "funnel":    snap.get("funnel", _MONITORING_SAFE_DEFAULT["funnel"]),
            "api":       snap.get("api", {}),
            "cache":     snap.get("cache", {}),
            "cycles":    snap.get("cycles", {}),
            "event_log": snap.get("event_log", []),
        })
    except Exception:
        logger.exception("/monitoring: unexpected error — returning safe default")
        return JSONResponse(content=_MONITORING_SAFE_DEFAULT)


# Include all scanner routes into the standalone app so this module works
# both as a standalone service and when embedded in the dashboard.
app.include_router(scanner_router)


# =============================================================================
# GLOBAL EXCEPTION HANDLERS  [P2-SCN-V2.7B]
# =============================================================================

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Normalise FastAPI HTTPException (404, 405, etc.) to our JSON shape."""
    logger.warning(
        "HTTP %s on %s %s: %s",
        exc.status_code, request.method, request.url.path, exc.detail,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "status":    "error",
            "message":   str(exc.detail),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Catch-all for any unhandled exception that escapes an endpoint.
    Logs the full traceback and returns a structured JSON error.
    All individual endpoints already have their own try/except, so this
    handler is a last-resort safety net only.
    """
    logger.exception(
        "Unhandled exception on %s %s: %s",
        request.method, request.url.path, exc,
    )
    return JSONResponse(
        status_code=500,
        content={
            "status":    "error",
            "message":   str(exc) or "internal server error",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Return consistent JSON shape for FastAPI request-validation failures."""
    logger.warning(
        "Validation error on %s %s: %s",
        request.method, request.url.path, exc,
    )
    return JSONResponse(
        status_code=422,
        content={
            "status":    "error",
            "message":   str(exc),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


# =============================================================================
# STARTUP SELF-TEST  [P2-SCN-V2.7A]
# =============================================================================

async def _run_startup_selftest() -> None:
    """
    Verify storage, required files, scanner instance, background loop,
    and registered routes.  Prints a human-readable summary to stdout/log.
    Failures are logged but never raise — the service starts regardless.
    """
    from pathlib import Path as _Path

    checks: dict[str, bool] = {}

    # ── 1. Storage writable ────────────────────────────────────────────────
    try:
        probe = _Path(STORAGE_DIR) / ".startup_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        checks["storage_writable"] = True
    except Exception:
        logger.exception("Startup self-test: storage write failed")
        checks["storage_writable"] = False

    # ── 2. Required JSON files ──────────────────────────────────────────────
    for fname in ("watchlist.json", "signals.json", "stats.json"):
        checks[fname] = (_Path(STORAGE_DIR) / fname).is_file()

    # ── 3. Scanner instance and background loop ────────────────────────────
    # Create the task now and yield once so _scanner_loop runs up to its
    # first await — by then _SCANNER and _TRACKER are both assigned.
    global _SCANNER_TASK
    task = asyncio.create_task(_scanner_loop())
    logger.info(f"SCANNER TASK CREATED: {task}")
    _SCANNER_TASK = task
    await asyncio.sleep(0)          # yield → task runs to first await
    logger.info(
          f"TASK DONE={task.done()} "
          f"CANCELLED={task.cancelled()}"
    )
    checks["scanner_instance"] = _SCANNER is not None
    checks["background_loop"]  = not task.done()   # still alive

    # ── 4. API routes registered ───────────────────────────────────────────
    route_paths = [
        getattr(r, "path", "") for r in app.routes
    ]
    required_routes = {
        "/health",
        "/api/v1/scanner/signals",
        "/api/v1/scanner/market-state",
        "/api/v1/scanner/status",
        "/api/v1/scanner/metrics",
        "/api/v1/scanner/watchlist",
    }
    checks["api_routes"] = required_routes.issubset(set(route_paths))

    # ── Summary ────────────────────────────────────────────────────────────
    tick = lambda ok: "✅" if ok else "❌"

    storage_ok = (
        checks["storage_writable"]
        and checks["watchlist.json"]
        and checks["signals.json"]
        and checks["stats.json"]
    )
    scanner_ok = checks["scanner_instance"]
    loop_ok    = checks["background_loop"]
    api_ok     = checks["api_routes"]

    lines = [
        "🚀 Scanner V2 Started",
        f"   Storage Ready           {tick(storage_ok)}",
        f"     writable={checks['storage_writable']}  "
        f"watchlist.json={checks['watchlist.json']}  "
        f"signals.json={checks['signals.json']}  "
        f"stats.json={checks['stats.json']}",
        f"   Scanner Ready           {tick(scanner_ok)}",
        f"   Background Loop Ready   {tick(loop_ok)}",
        f"   API Ready               {tick(api_ok)}",
    ]
    for line in lines:
        logger.info(line)

    all_ok = storage_ok and scanner_ok and loop_ok and api_ok
    if not all_ok:
        logger.warning("Startup self-test: one or more checks FAILED — see details above")
    else:
        logger.info("Startup self-test: all checks passed")


# =============================================================================
# PERSISTENCE HELPERS  [P2-SCN-V2.7C + V2.7D]
# =============================================================================

# Source-of-truth file paths (constructed from imported STORAGE_DIR)
_WATCHLIST_FILE = os.path.join(STORAGE_DIR, "watchlist.json")

# I-12: All persistence files — backed up hourly and on graceful shutdown.
# NEVER overwrite an existing file on startup; only add missing ones.
_BACKUP_PAIRS = [
    (SIGNAL_LOG_FILE,       "signals_backup.json"),
    (STATS_FILE,            "stats_backup.json"),
    (_WATCHLIST_FILE,       "watchlist_backup.json"),
    (LIVE_SIGNALS_FILE,     "live_signals_backup.json"),
    (SIGNAL_HISTORY_FILE,   "signal_history_backup.json"),
    (COIN_PERFORMANCE_FILE, "coin_performance_backup.json"),
    (TIER_ACCURACY_FILE,    "tier_accuracy_backup.json"),
    (SETTINGS_FILE,         "settings_backup.json"),
]


async def _do_backup(*, label: str = "Backup") -> None:
    """
    Atomically copy each source file into BACKUP_DIR/<name>_backup.json.
    Write to a .tmp sidecar first, then os.replace() — crash-safe.
    Runs in a thread-pool executor so it never blocks the event loop.
    """
    import json as _json
    from pathlib import Path as _Path

    backup_dir = _Path(BACKUP_DIR)
    backup_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, bool] = {}
    for src_path, dst_name in _BACKUP_PAIRS:
        short = dst_name.replace("_backup.json", "")
        dst   = backup_dir / dst_name
        tmp   = dst.with_suffix(".tmp")
        try:
            src = _Path(src_path)
            # Read and write both offloaded so neither blocks the event loop.
            def _read_and_write(s=src, t=tmp, f=dst):
                d = s.read_bytes() if s.is_file() else b"[]"
                t.write_bytes(d)
                t.replace(f)
            logger.debug("[scanner] offloading backup read+write for %s to thread", short)
            await asyncio.get_running_loop().run_in_executor(None, _read_and_write)
            results[short] = True
        except Exception:
            logger.exception("%s: failed to write %s", label, dst_name)
            results[short] = False

    tick = lambda ok: "✅" if ok else "❌"
    logger.info("%s complete:", label)
    for name, ok in results.items():
        logger.info("  %-12s %s", name, tick(ok))
    if not all(results.values()):
        logger.warning("%s: one or more files failed — see above", label)


def _run_cleanup() -> dict:
    """Remove expired signals from live_signals.json using tier-based TTL.

    I-04 rules:  MEDIUM → 24 h  |  HIGH → 72 h  |  ELITE → 168 h
    Preserves signal_history.json, coin_performance.json, tier_accuracy.json.
    Updates LATEST_MTB_SIGNALS and _CLEANUP_STATS in-process.
    """
    global LATEST_SCANNER_SIGNALS, LATEST_MTB_SIGNALS, _CLEANUP_STATS
    import json as _json

    now = datetime.now(timezone.utc)

    # Initialise so the logging block below is always safe even on early error.
    alive:   list[dict] = []
    expired: list[dict] = []

    # ── Atomic read-modify-write (NF-10: lost-update race fix) ───────────────
    # Holding _write_json_lock for the FULL read→filter→write sequence means
    # the scanner's write_json_safely() call cannot interleave between our
    # file read and our file write, so no fresh signal can be silently dropped.
    # _write_json_lock is RLock, so write_json_safely() can re-acquire it on
    # this same thread without deadlocking.
    try:
        from bots.scanner_bot.scanner import _write_json_lock, write_json_safely, Path
        with _write_json_lock:
            stored: list[dict] = []
            try:
                with open(LIVE_SIGNALS_FILE, "r", encoding="utf-8") as _f:
                    stored = _json.load(_f).get("signals", [])
            except FileNotFoundError:
                logger.warning("Cleanup: live_signals.json not found — treating as empty")
            except Exception:
                logger.exception("Cleanup: failed to read live_signals.json — falling back to in-memory signals")
                stored = list(LATEST_MTB_SIGNALS)

            alive   = [s for s in stored if     _signal_is_alive(s, now)]
            expired = [s for s in stored if not _signal_is_alive(s, now)]

            write_json_safely(Path(LIVE_SIGNALS_FILE), {"signals": alive})
            LATEST_SCANNER_SIGNALS = alive
            LATEST_MTB_SIGNALS     = LATEST_SCANNER_SIGNALS
    except Exception:
        logger.exception("Cleanup: failed to write live_signals.json")

    # Update in-memory stats
    next_ts = (now + timedelta(seconds=_CLEANUP_INTERVAL_SECONDS)).isoformat()
    _CLEANUP_STATS = {
        "last_cleanup_time":      now.isoformat(),
        "next_cleanup_time":      next_ts,
        "expired_last_run":       len(expired),
        "active_count":           len(alive),
        "total_expired_lifetime": _CLEANUP_STATS.get("total_expired_lifetime", 0) + len(expired),
    }

    if expired:
        logger.info(
            "Cleanup: removed %d expired signal(s) — %d remaining",
            len(expired), len(alive),
        )
    else:
        logger.info("Cleanup: 0 expired; %d active signal(s)", len(alive))

    return _CLEANUP_STATS


async def _cleanup_loop() -> None:
    """Background task: expire old signals every _CLEANUP_INTERVAL_SECONDS (10 min)."""
    global _CLEANUP_STATS
    logger.info("Cleanup loop started (interval=%ds)", _CLEANUP_INTERVAL_SECONDS)
    # Set initial next-cleanup time so dashboard shows it immediately
    _CLEANUP_STATS["next_cleanup_time"] = (
        datetime.now(timezone.utc) + timedelta(seconds=_CLEANUP_INTERVAL_SECONDS)
    ).isoformat()
    while True:
        await asyncio.sleep(_CLEANUP_INTERVAL_SECONDS)
        try:
            logger.debug("[scanner] offloading _run_cleanup to thread")
            await asyncio.to_thread(_run_cleanup)
        except Exception:
            logger.exception("Cleanup loop: unexpected error in _run_cleanup")


async def _backup_loop() -> None:
    """Hourly backup — sleeps first so it doesn't duplicate the startup check."""
    interval = int(os.getenv("BACKUP_INTERVAL_SECONDS", "3600"))
    logger.info("Backup loop started (interval=%ds)", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            await _do_backup(label="Hourly backup")
        except Exception:
            logger.exception("Backup loop: unexpected error in _do_backup")


async def _do_shutdown_save() -> None:
    """
    Called by the shutdown lifespan event.  Saves ALL persistence files,
    cancels background tasks, then logs completion.
    Never raises — errors are caught per-file.
    I-12: All 7 critical files covered via _BACKUP_PAIRS.
    """
    from pathlib import Path as _Path

    logger.info("Scanner shutting down...")

    # ── 1. Save all files ──────────────────────────────────────────────────
    backup_dir = _Path(BACKUP_DIR)
    backup_dir.mkdir(parents=True, exist_ok=True)

    for src_path, dst_name in _BACKUP_PAIRS:
        dst = backup_dir / dst_name
        tmp = dst.with_suffix(".tmp")
        label = dst_name.replace("_backup.json", "").replace("_", " ").title()
        try:
            src  = _Path(src_path)
            data = src.read_bytes() if src.is_file() else b"{}"
            tmp.write_bytes(data)
            tmp.replace(dst)
            msg = f"{label} saved ✅"
        except Exception:
            logger.exception("Shutdown save failed: %s", dst_name)
            msg = f"{label} save failed ❌"
        logger.info(msg)

    # ── 2. Cancel background tasks ─────────────────────────────────────────
    for task_ref, name in ((_SCANNER_TASK, "scanner loop"),
                           (_BACKUP_TASK,  "backup loop"),
                           (_CLEANUP_TASK, "cleanup loop")):
        if task_ref is not None and not task_ref.done():
            task_ref.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task_ref), timeout=3.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            logger.info("Stopped %s ✅", name)

    logger.info("Shutdown complete ✅")


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    logger.info("Starting MTB Scanner API on 0.0.0.0:%d", port)
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info",
    )
