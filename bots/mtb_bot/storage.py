"""
PROJECT-ALPHA MTB Bot local JSON storage.

Replaces notebook Google Drive persistence with local PROJECT-ALPHA storage files:
data/watchlist.json, data/positions.json, data/trades.json, data/stats.json.
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("mtb_bot.storage")

# One lock per state file — used in every save_* function body.
_positions_lock = threading.Lock()
_trades_lock    = threading.Lock()
_stats_lock     = threading.Lock()

from .config import (
    DATA_DIR,
    INITIAL_CASH_BALANCE,
    POSITIONS_FILE,
    STATS_FILE,
    TRADE_AMOUNT,
    TRADES_FILE,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists() or path.stat().st_size == 0:
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        backup = path.with_suffix(path.suffix + ".bak")
        if backup.exists():
            try:
                return json.loads(backup.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        return default


def _write_json(path: Path, data: Any) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
        except OSError:
            pass
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)


def ensure_storage() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not POSITIONS_FILE.exists():
        _write_json(POSITIONS_FILE, {"positions": []})
    if not TRADES_FILE.exists():
        _write_json(TRADES_FILE, {"trades": []})
    if not STATS_FILE.exists():
        _write_json(
            STATS_FILE,
            {
                "cash_balance": INITIAL_CASH_BALANCE,
                "trade_amount": TRADE_AMOUNT,
                "total_pnl": 0.0,
                "daily_pnl": 0.0,
                "last_updated": utc_now(),
            },
        )


def _scanner_watchlist() -> list[str]:
    """Read the unified scanner watchlist (single source of truth)."""
    try:
        from bots.scanner_bot.scanner import get_watchlist
        wl = get_watchlist()
        coins = wl.get("coins", [])
        return [str(c).upper().strip() for c in coins if str(c).strip()]
    except Exception:
        # NF-8: watchlist read failed — snapshot() returns empty watchlist rather than crashing.
        logger.exception("MTB _scanner_watchlist: failed to read scanner watchlist — returning []")
        return []


def load_positions() -> list[dict]:
    ensure_storage()
    data = _read_json(POSITIONS_FILE, {"positions": []})
    positions = data.get("positions", data if isinstance(data, list) else [])
    return positions if isinstance(positions, list) else []


def save_positions(positions: list[dict]) -> None:
    with _positions_lock:
        _write_json(POSITIONS_FILE, {"positions": positions})


def load_trades() -> list[dict]:
    ensure_storage()
    data = _read_json(TRADES_FILE, {"trades": []})
    trades = data.get("trades", data if isinstance(data, list) else [])
    return trades if isinstance(trades, list) else []


def save_trades(trades: list[dict]) -> None:
    with _trades_lock:
        _write_json(TRADES_FILE, {"trades": trades})


def load_stats() -> dict:
    ensure_storage()
    data = _read_json(STATS_FILE, {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("cash_balance", INITIAL_CASH_BALANCE)
    data.setdefault("trade_amount", TRADE_AMOUNT)
    data.setdefault("total_pnl", 0.0)
    data.setdefault("daily_pnl", 0.0)
    data.setdefault("last_updated", utc_now())
    return data


def save_stats(stats: dict) -> None:
    with _stats_lock:
        stats = dict(stats)
        stats["last_updated"] = utc_now()
        _write_json(STATS_FILE, stats)


def update_stats(fn: Callable[[dict], None]) -> dict:
    """Atomically load, modify, and save stats under _stats_lock.

    Holds *_stats_lock* for the ENTIRE read → modify → write sequence so
    that concurrent callers cannot produce a lost-update on cash_balance or
    any other stats field.

    ``fn(stats)`` is called with the current (normalised) stats dict and
    must mutate it in-place.  ``fn`` must not call ``load_stats`` or
    ``save_stats`` (that would deadlock on *_stats_lock*).

    Returns the saved stats dict (after ``last_updated`` is stamped).

    This is the ONLY safe way to mutate stats from the trading engine.
    Read-only callers (``snapshot``, ``validate_signal``) may continue to
    use ``load_stats()``.
    """
    with _stats_lock:
        ensure_storage()
        data = _read_json(STATS_FILE, {})
        if not isinstance(data, dict):
            data = {}
        data.setdefault("cash_balance",  INITIAL_CASH_BALANCE)
        data.setdefault("trade_amount",  TRADE_AMOUNT)
        data.setdefault("total_pnl",     0.0)
        data.setdefault("daily_pnl",     0.0)
        data.setdefault("last_updated",  utc_now())
        fn(data)
        data["last_updated"] = utc_now()
        _write_json(STATS_FILE, data)
        return dict(data)


def get_open_positions() -> list[dict]:
    return [p for p in load_positions() if str(p.get("status", "")).upper() == "OPEN"]


def get_closed_trades() -> list[dict]:
    trades = load_trades()
    return [t for t in trades if str(t.get("status", "")).upper() == "CLOSED"]


def snapshot() -> dict:
    from .config import BOT_MODE
    positions = load_positions()
    trades = load_trades()
    stats = load_stats()
    open_positions = [p for p in positions if str(p.get("status", "")).upper() == "OPEN"]
    closed_trades = [t for t in trades if str(t.get("status", "")).upper() == "CLOSED"]
    return {
        "status": "ONLINE",
        "mode": BOT_MODE,
        "open_positions": open_positions,
        "closed_trades": closed_trades,
        "daily_pnl": round(float(stats.get("daily_pnl", 0.0)), 4),
        "trade_amount": float(stats.get("trade_amount", TRADE_AMOUNT)),
        "cash_balance": round(float(stats.get("cash_balance", 0.0)), 4),
        "total_pnl": round(float(stats.get("total_pnl", 0.0)), 4),
        "watchlist": _scanner_watchlist(),
        "last_updated": stats.get("last_updated"),
    }


ensure_storage()
