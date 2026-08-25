import asyncio
from datetime import datetime, timezone
import hmac
import json
import logging
import os
import threading
import time as _time
from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from bots.scanner_bot.scanner import get_signals, get_live_signals

from bots.scanner_bot.scanner import get_watchlist, WatchlistStore
from bots.scanner_bot.scanner import get_stats
from bots.scanner_bot.scanner import get_market_state, get_signal_stats, get_performance_stats, get_performance_signals, get_per_coin_performance, get_signal_history, get_signal_history_stats, get_coin_performance_data, get_coin_performance_stats, get_tier_accuracy_data, get_tier_accuracy_stats
import bots.scanner_bot.main as scanner_main
from bots.scanner_bot.main import scanner_router
import bots.mtb_bot.main as mtb_main
import bots.pmb_bot.main as pmb_main
import bots.volatile_gridX.main as vgx_main
import bots.scanner_bot.telegram_bot as scanner_tg
import bots.volatile_gridX.vgx_telegram_bot as vgx_tg
import bots.pmb_bot.pmb_telegram_bot as pmb_tg
import bots.mtb_bot.mtb_telegram_bot as mtb_tg
from bots.mtb_bot.storage import snapshot as mtb_snapshot
from bots.pmb_bot.storage import snapshot as pmb_snapshot
from bots.risk_engine.engine import snapshot as risk_snapshot
from bots.risk_engine.runtime_state import set_trading_enabled as _risk_set_trading_enabled
from bots.risk_engine.config import (
    EMERGENCY_STOP as _EMERGENCY_STOP,
    get_trading_enabled as _get_trading_enabled,
    set_trading_enabled as _set_trading_enabled_imem,
)

from bots.scanner_bot.scanner import get_watchlist as _scanner_get_watchlist, resolve_coin_pair as _resolve_coin_pair
from bots.scanner_bot import watchlist_ops as _watchlist_ops
from bots.volatile_gridX.config import get_vgx_storage_file as _get_vgx_storage_file
from bots.volatile_gridX.storage import (
    get_grid_config       as _vgx_get_grid_config,
    get_coin_base_price   as _vgx_get_coin_base_price,  # noqa: F401 — available for future engine use
    set_coin_base_price   as _vgx_set_coin_base_price,
    remove_coin_base_price as _vgx_remove_coin_base_price,
    get_grid_coins        as _vgx_get_grid_coins,
    set_grid_coins        as _vgx_set_grid_coins,
)

# ── In-memory trading-toggle metadata ─────────────────────────────────────────
# Tracks who last changed the toggle and when (for /api/v1/trading/status).
# Reset to "env_var" on every process restart.
_trading_meta_lock: threading.Lock = threading.Lock()
_trading_changed_by: str = "env_var"
_trading_changed_at: str = datetime.now(timezone.utc).isoformat()


def vgx_snapshot() -> dict:
    """
    Build a dashboard-ready VGX snapshot by reading the storage JSON directly.
    Uses get_vgx_storage_file() so the path is always consistent with what the
    VGX bot and risk engine use.  Returns safe defaults if the file is absent.
    """
    raw: dict = {}
    _vgx_file = _get_vgx_storage_file()
    try:
        if os.path.exists(_vgx_file) and os.path.getsize(_vgx_file) > 0:
            with open(_vgx_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        raw = {}

    virtual_balance = float(raw.get("virtual_balance", 1_000_000))
    positions_dict  = raw.get("positions", {})
    trade_log       = raw.get("trade_log",  [])

    open_positions = [
        {
            "coin":             p.get("coin", key.split("_")[0]),
            "buy_price":        round(float(p.get("buy_price", 0)), 4),
            "qty":              round(float(p.get("qty",       0)), 8),
            "amount":           round(float(p.get("amount",    0)), 2),
            "trailing_active":  bool(p.get("trailing_active", False)),
            "source":           p.get("trade_source", "SCANNER"),
        }
        for key, p in (positions_dict.items() if isinstance(positions_dict, dict) else [])
    ]

    sell_trades = [t for t in trade_log if "SELL" in str(t.get("action", "")).upper()]
    wins   = sum(1 for t in sell_trades if float(t.get("pnl", 0)) > 0)
    losses = sum(1 for t in sell_trades if float(t.get("pnl", 0)) < 0)
    total  = len(sell_trades)
    win_rate = round(wins / total * 100, 1) if total > 0 else 0.0

    from datetime import date
    today_str = date.today().isoformat()
    daily_pnl = round(sum(
        float(t.get("pnl", 0))
        for t in sell_trades
        if str(t.get("time", "")).startswith(today_str)
    ), 2)
    total_pnl = round(sum(float(t.get("pnl", 0)) for t in sell_trades), 2)

    last_trade: dict = {}
    if trade_log:
        lt = trade_log[-1]
        last_trade = {
            "time":   lt.get("time",   ""),
            "coin":   lt.get("coin",   ""),
            "action": lt.get("action", ""),
            "price":  lt.get("price",  0),
            "amount": lt.get("amount", 0),
            "pnl":    lt.get("pnl",    0),
        }

    vgx_mode = os.getenv("VGX_BOT_MODE", "PAPER")
    trade_amount = float(os.getenv("VGX_TRADE_AMOUNT", os.getenv("TRADE_AMOUNT", "110")))

    portfolio_hist = raw.get("portfolio_history", [])
    if portfolio_hist:
        _recent = portfolio_hist[-60:]
        equity_labels = [str(h.get("time", ""))[:16] for h in _recent]
        equity_data   = [round(float(h.get("portfolio", virtual_balance)), 2) for h in _recent]
    else:
        equity_labels = ["Start"]
        equity_data   = [round(virtual_balance, 2)]

    return {
        "status":          vgx_mode,
        "virtual_balance": round(virtual_balance, 2),
        "daily_pnl":       daily_pnl,
        "total_pnl":       total_pnl,
        "open_positions":  open_positions,
        "grid_levels":     len(_vgx_get_grid_coins()),
        "grid_coins":      _vgx_get_grid_coins(),
        "last_trade":      last_trade,
        "win_rate":        win_rate,
        "wins":            wins,
        "losses":          losses,
        "paper_trades":    len(trade_log),
        "trade_amount":    trade_amount,
        "target_pct":      5.0,
        "stop_loss_pct":   5.0,
        "equity_curve": {
            "labels": equity_labels,
            "data":   equity_data,
        },
        "win_loss_chart": {
            "labels": ["Wins", "Losses"],
            "data":   [wins, losses],
        },
    }


from contextlib import asynccontextmanager
from datetime import datetime, timezone

logger = logging.getLogger("app")
_APP_START_TIME = _time.time()


# ═══════════════════════════════════════════════════════════════
#  API KEY AUTH — fail-closed
# ═══════════════════════════════════════════════════════════════

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

DASHBOARD_API_KEY = os.getenv("DASHBOARD_API_KEY")

# Paths that bypass X-API-Key auth. Kept minimal after the auth hardening:
# - /health: uptime probe, no sensitive data
# - /: browser navigation; session gate is enforced inside the route handler
# - /login, /logout: auth flow — must be reachable without a key
#
# All /api/* routes now require X-API-Key (sent automatically by
# authenticatedFetch in script.js), so they are no longer exempt.
_DASHBOARD_EXEMPT_PATHS = frozenset({
    "/health",
    "/",        # browser navigation — session check is enforced inside the route handler
    "/login",
    "/logout",
})

if not DASHBOARD_API_KEY:
    logger.warning(
        "DASHBOARD_API_KEY is not set — all protected endpoints will return 401. "
        "Set this environment variable before accepting traffic."
    )

    async def require_api_key(request: Request, api_key: str = Depends(api_key_header)) -> str:
        if request.url.path in _DASHBOARD_EXEMPT_PATHS:
            return ""
        logger.warning(
            "Auth denied — DASHBOARD_API_KEY not configured [path=%s method=%s]",
            request.url.path,
            request.method,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "unauthorized", "reason": "DASHBOARD_API_KEY not configured"},
        )
else:
    async def require_api_key(request: Request, api_key: str = Depends(api_key_header)) -> str:
        if request.url.path in _DASHBOARD_EXEMPT_PATHS:
            return ""
        # Constant-time comparison prevents timing-based key enumeration.
        if not api_key or not hmac.compare_digest(api_key, DASHBOARD_API_KEY):
            logger.warning(
                "Auth denied — invalid or missing X-API-Key [path=%s method=%s]",
                request.url.path,
                request.method,
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": "unauthorized", "reason": "Invalid or missing X-API-Key header"},
            )
        logger.info(
            "Auth accepted [path=%s method=%s]",
            request.url.path,
            request.method,
        )
        return api_key


# ═══════════════════════════════════════════════════════════════
#  SNAPSHOT CACHE — 3-second TTL, asyncio.to_thread offloading
# ═══════════════════════════════════════════════════════════════

_SNAPSHOT_CACHE: dict[str, tuple[float, dict]] = {}
_SNAPSHOT_TTL = 3.0
_SNAPSHOT_CACHE_LOCK = asyncio.Lock()


async def _cached_snapshot(key: str, fn) -> dict:
    """Return a cached snapshot, refreshing via asyncio.to_thread when stale."""
    entry = _SNAPSHOT_CACHE.get(key)
    if entry and (_time.monotonic() - entry[0]) < _SNAPSHOT_TTL:
        return entry[1]
    result: dict = await asyncio.to_thread(fn)
    async with _SNAPSHOT_CACHE_LOCK:
        _SNAPSHOT_CACHE[key] = (_time.monotonic(), result)
    return result


@asynccontextmanager
async def _app_lifespan(app: FastAPI):
    """Root dashboard lifespan — starts all bot background tasks."""
    await scanner_main.startup_event()
    await mtb_main.startup_event()
    await pmb_main.startup_event()
    await vgx_main.startup_event()
    try:
        await scanner_tg.startup_event()
    except Exception as e:
        logger.warning("Scanner Telegram bot failed to start: %s", e)
    try:
        await vgx_tg.startup_event()
    except Exception as e:
        logger.warning("VGX Telegram bot failed to start: %s", e)
    try:
        await pmb_tg.startup_event()
    except Exception as e:
        logger.warning("PMB Telegram bot failed to start: %s", e)
    try:
        await mtb_tg.startup_event()
    except Exception as e:
        logger.warning("MTB Telegram bot failed to start: %s", e)
    # AlertManager smoke-check
    try:
        from monitoring.telegram_alerts import AlertManager
        _am = AlertManager()
        logger.info("AlertManager ready — ALERT_BOT_TOKEN=%s",
                    "SET" if _am._telegram._bot_token else "UNSET")
    except Exception as e:
        logger.warning("AlertManager not available: %s", e)
    yield
    await mtb_tg.shutdown_event()
    await pmb_tg.shutdown_event()
    await vgx_tg.shutdown_event()
    await scanner_tg.shutdown_event()
    await vgx_main.shutdown_event()
    await pmb_main.shutdown_event()
    await mtb_main.shutdown_event()
    # scanner shutdown handled by its own _do_shutdown_save in scanner_main


app = FastAPI(
    title="PROJECT-ALPHA ULTIMATE DASHBOARD Framework",
    lifespan=_app_lifespan,
    dependencies=[Depends(require_api_key)],
)

# Session middleware — must be added immediately after app creation so the
# session is available in every route handler, including the login flow.
_SESSION_SECRET = os.getenv("SESSION_SECRET")
if not _SESSION_SECRET:
    raise RuntimeError("SESSION_SECRET environment variable is not set")
# https_only=True enforces the Secure cookie flag in production (HTTPS).
# Disabled only for local HTTP development; set ENVIRONMENT=production to enable.
_HTTPS_ONLY = os.getenv("ENVIRONMENT", "development").lower() == "production"
app.add_middleware(
    SessionMiddleware,
    secret_key=_SESSION_SECRET,
    https_only=_HTTPS_ONLY,
    same_site="lax",
    max_age=28800,  # 8-hour session expiry
)


@app.get("/health", include_in_schema=False)
async def health_probe():
    return {"status": "ok"}


# Mount all /api/v1/scanner/* routes from the scanner bot into the dashboard app.
app.include_router(scanner_router)

app.mount(
    "/static",
    StaticFiles(directory="dashboard/static"),
    name="static"
)
templates = Jinja2Templates(directory="dashboard/templates")

def _get_uptime() -> str:
    seconds = int(_time.time() - _APP_START_TIME)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    return f"{days}d {hours}h {minutes}m"


def _get_cpu_usage() -> str:
    try:
        import psutil
        return f"{psutil.cpu_percent(interval=None):.1f}%"
    except Exception:
        return "N/A"


def _get_memory_usage() -> str:
    try:
        import psutil
        mem = psutil.virtual_memory()
        return f"{mem.used // (1024 * 1024)}MB"
    except Exception:
        return "N/A"


def _get_health_pct() -> int:
    try:
        scanner_ok = (
            getattr(scanner_main, "_SCANNER_TASK", None) is not None
            and not getattr(scanner_main, "_SCANNER_TASK").done()
        )
        tg_ok = any([
            getattr(scanner_tg, "_SCANNER_TG_APP", None),
            getattr(vgx_tg, "_VGX_TG_APP", None),
            getattr(pmb_tg, "_PMB_TG_APP", None),
            getattr(mtb_tg, "_MTB_TG_APP", None),
        ])
        return 100 if (scanner_ok and tg_ok) else 50
    except Exception:
        return 0


def _compute_market_strength(signals: list) -> int:
    """Return a 0-100 integer gauge value derived from live signal confidence scores."""
    if not signals:
        return 0
    scores = [s.get("confidence", s.get("score", 0)) for s in signals]
    avg = sum(scores) / len(scores)
    # confidence/score are already 0-100 scale; clamp to int
    return max(0, min(100, int(avg)))


def _build_charts_payload(signals: list) -> dict:
    """
    Build the charts sub-dict that script.js expects.
    All four Chart.js widgets read from this structure.
    """
    # --- distribution (doughnut) ---
    # Count signals by priority tier: ELITE → High, STRONG SIGNAL → Medium, rest → Low
    tier_counts = {"High": 0, "Medium": 0, "Low": 0}
    for s in signals:
        tier = s.get("tier", s.get("priority", ""))
        if tier in ("ELITE", "HIGH"):
            tier_counts["High"] += 1
        elif tier in ("STRONG SIGNAL", "MEDIUM"):
            tier_counts["Medium"] += 1
        else:
            tier_counts["Low"] += 1

    # --- daily_signals (line) ---
    # Group signal counts by date from timestamps in the latest scanner signals
    from collections import defaultdict
    day_counts: dict = defaultdict(int)
    for s in signals:
        ts = s.get("timestamp", "")
        if ts:
            try:
                day = ts[:10]   # "YYYY-MM-DD"
                day_counts[day] += 1
            except Exception:
                pass
    sorted_days = sorted(day_counts.keys())
    daily_labels = sorted_days if sorted_days else ["--"]
    daily_data   = [day_counts[d] for d in sorted_days] if sorted_days else [0]

    # --- asset_allocation (pie) ---
    # Distribute signals by coin_class if available, else by coin name
    allocation: dict = defaultdict(int)
    for s in signals:
        key = s.get("coin_class", s.get("coin", "OTHER"))
        allocation[key] += 1
    alloc_labels = list(allocation.keys()) if allocation else ["No Data"]
    alloc_data   = list(allocation.values()) if allocation else [1]

    # --- portfolio_growth (line) ---
    # Placeholder cumulative signal count curve (filled by live trading data when available)
    growth_labels = daily_labels
    cumulative = 0
    growth_data = []
    for d in sorted_days:
        cumulative += day_counts[d]
        growth_data.append(cumulative)
    if not growth_data:
        growth_labels = ["--"]
        growth_data   = [0]

    return {
        "distribution": {
            "labels": list(tier_counts.keys()),
            "data":   list(tier_counts.values()),
        },
        "daily_signals": {
            "labels": daily_labels,
            "data":   daily_data,
        },
        "asset_allocation": {
            "labels": alloc_labels,
            "data":   alloc_data,
        },
        "portfolio_growth": {
            "labels": growth_labels,
            "data":   growth_data,
        },
    }


# ── Trade history enrichment helpers ─────────────────────────────────────────

def _compute_holding_time(entry_ts: str, exit_ts: str) -> str:
    """Return human-readable duration between two ISO-8601 timestamps."""
    try:
        from datetime import timezone as _tz
        t_in  = datetime.fromisoformat(entry_ts)
        t_out = datetime.fromisoformat(exit_ts)
        if t_in.tzinfo  is None: t_in  = t_in.replace(tzinfo=_tz.utc)
        if t_out.tzinfo is None: t_out = t_out.replace(tzinfo=_tz.utc)
        diff = int((t_out - t_in).total_seconds())
        if diff < 0:     return "—"
        if diff < 60:    return f"{diff}s"
        if diff < 3600:  return f"{diff // 60}m {diff % 60}s"
        if diff < 86400: return f"{diff // 3600}h {(diff % 3600) // 60}m"
        return f"{diff // 86400}d {(diff % 86400) // 3600}h"
    except Exception:
        return "—"


def _enrich_closed_trades(
    trades: list[dict],
    all_trades: list[dict],
    prices: dict | None = None,
) -> list[dict]:
    """Add ``pnl_pct``, ``holding_time``, ``entry_price``, ``exit_reason``,
    and ``current_price`` to closed trade records.

    Matches each sell/close trade to its corresponding buy trade via the shared
    position ``id`` field.  Falls back to ``'—'`` when no match is found.
    Does **not** mutate the input lists.

    ``prices`` is an optional ``{coin: float}`` mapping for current market prices
    (pre-fetched by the caller to avoid blocking the event loop).
    """
    _EXIT_REASON_MAP = {
        "TAKE_PROFIT":   "TAKE_PROFIT",
        "STOP_LOSS":     "STOP_LOSS",
        "TRAILING_STOP": "TRAILING_STOP",
        "MANUAL":        "MANUAL",
    }

    def _derive_exit_reason(t: dict) -> str:
        action       = str(t.get("action",       "")).upper()
        reason       = str(t.get("reason",       "")).upper()
        close_reason = str(t.get("close_reason", "")).upper()
        combined     = f"{action} {reason} {close_reason}"
        for key, label in _EXIT_REASON_MAP.items():
            if key in combined:
                return label
        if "PARTIAL_SELL" in action:
            return "TAKE_PROFIT"
        status = str(t.get("status", "")).upper()
        return "UNKNOWN" if status == "CLOSED" else "UNKNOWN"

    # Build id → earliest BUY trade for holding_time and entry_price lookups
    buy_by_id: dict[str, dict] = {}
    for t in all_trades:
        tid    = t.get("id", "")
        action = str(t.get("action", "")).upper()
        if tid and "BUY" in action and tid not in buy_by_id:
            buy_by_id[tid] = t

    enriched: list[dict] = []
    for trade in trades:
        t      = dict(trade)
        pnl    = float(t.get("pnl", 0) or 0)
        amount = float(t.get("amount", 0) or 0)
        cost   = amount - pnl  # proceeds − pnl = original cost basis

        # pnl_pct — prefer stored return_pct (already precise) when present
        if "return_pct" in t:
            t["pnl_pct"] = round(float(t["return_pct"]), 2)
        elif cost > 0:
            t["pnl_pct"] = round(pnl / cost * 100, 2)
        else:
            t["pnl_pct"] = 0.0

        # entry_price + holding_time from the matching BUY trade record
        buy = buy_by_id.get(t.get("id", ""))
        if buy:
            t.setdefault("entry_price", round(float(buy.get("price", 0) or 0), 6))
            t["holding_time"] = _compute_holding_time(
                buy.get("timestamp", ""), t.get("timestamp", "")
            )
        else:
            t.setdefault("entry_price", "—")
            t["holding_time"] = "—"

        # exit_reason — normalised from action / reason / close_reason
        t["exit_reason"] = _derive_exit_reason(t)

        # current_price — from caller-supplied prices dict (never blocks here)
        coin = t.get("coin") or ""
        if not coin:
            sym = str(t.get("symbol", ""))
            coin = sym.replace("B-", "").split("_")[0]
        t["current_price"] = (prices or {}).get(coin)

        enriched.append(t)
    return enriched


_TICKER_QUOTE_SUFFIXES = ("INR", "USDT", "BTC", "ETH")  # priority order


def _base_coin_from_market(market: str) -> str | None:
    """Extract the base coin symbol from a raw CoinDCX ``market`` string.

    CoinDCX ticker entries use concatenated pair strings with no separator
    (e.g. ``LINKINR``, ``LINKUSDT``, ``LINKBTC`` — NOT ``B-LINK_USDT``), so the
    base coin must be recovered by stripping a known quote-currency suffix.
    """
    m = str(market or "").upper()
    for suffix in _TICKER_QUOTE_SUFFIXES:
        if m.endswith(suffix) and len(m) > len(suffix):
            return m[: -len(suffix)]
    return None


def _read_scanner_ticker_cache() -> dict[str, float]:
    """Return ``{COIN: last_price}`` read strictly from ``_SCANNER._ticker_cache``.

    Zero CoinDCX (or any other) API calls — this only ever looks at the
    scanner's already-populated in-memory ticker cache. If the cache is
    empty/unavailable, returns ``{}`` (caller must treat missing coins as
    "price unavailable", never fall back to a network call).
    """
    import bots.scanner_bot.main as _scanner_main

    sc      = getattr(_scanner_main, "_SCANNER", None)
    tickers = getattr(sc, "_ticker_cache", None) if sc is not None else None
    if not tickers:
        return {}
    prices: dict[str, float] = {}
    for suffix in _TICKER_QUOTE_SUFFIXES:   # INR quoted prices win over USDT/BTC
        for entry in tickers:
            market = str(entry.get("market", ""))
            if not market.upper().endswith(suffix):
                continue
            base = _base_coin_from_market(market)
            if not base or base in prices:
                continue
            try:
                prices[base] = float(entry["last_price"])
            except (KeyError, ValueError, TypeError):
                continue
    return prices


async def _get_scanner_ticker_prices_only(coins: list[str]) -> dict[str, float | None]:
    """Return ``{coin: price | None}`` for each requested coin, ticker-cache-only.

    Unlike ``_get_current_prices_safe`` (which falls back to a live CoinDCX
    API call), this NEVER makes a network request. Used by any feature that
    is explicitly scoped to "no CoinDCX API calls" — e.g. PMB Open Positions
    live PnL — so a cold/empty ticker cache correctly surfaces as "price
    unavailable" rather than triggering a blocking HTTP fetch.
    """
    if not coins:
        return {}
    try:
        prices = await asyncio.to_thread(_read_scanner_ticker_cache)
    except Exception:
        prices = {}
    return {c: prices.get(str(c).upper()) for c in coins}


def _enrich_open_positions_live_pnl(
    positions: list[dict],
    prices: dict[str, float | None],
) -> list[dict]:
    """Decorate PMB open positions with live current_price / live_pnl fields.

    Open trade log fields (avg_entry_price, total_quantity, total_invested,
    dip_count, ...) come straight from storage and are left untouched — this
    only *adds* display-only keys on top:

      current_price   — from the scanner ticker cache (or None if unavailable)
      live_pnl         — current_value - invested_amount
      live_pnl_pct     — ((current_price - avg_entry_price) / avg_entry_price) * 100
      live_pnl_status  — "profit" | "loss" | "flat" | "unavailable" (for badge color)

    If a price is unavailable for a coin, current_price / live_pnl /
    live_pnl_pct are all set to ``None`` (rendered as "—") — no partial or
    guessed values, per spec. Does not mutate the input list.
    """
    enriched: list[dict] = []
    for pos in positions:
        p = dict(pos)
        coin      = str(p.get("coin", "")).upper()
        avg_entry = float(p.get("avg_entry_price", 0) or 0)
        qty_held  = float(p.get("total_quantity", 0) or 0)
        invested  = float(p.get("total_invested", 0) or 0)

        price = prices.get(coin) if coin else None

        if price is None or avg_entry <= 0:
            p["current_price"]  = None
            p["live_pnl"]       = None
            p["live_pnl_pct"]   = None
            p["live_pnl_status"] = "unavailable"
        else:
            current_value = price * qty_held
            live_pnl      = current_value - invested
            live_pnl_pct  = ((price - avg_entry) / avg_entry) * 100

            p["current_price"] = round(price, 6)
            p["live_pnl"]      = round(live_pnl, 4)
            p["live_pnl_pct"]  = round(live_pnl_pct, 2)

            if live_pnl > 0:
                p["live_pnl_status"] = "profit"
            elif live_pnl < 0:
                p["live_pnl_status"] = "loss"
            else:
                p["live_pnl_status"] = "flat"

        enriched.append(p)
    return enriched


async def _get_current_prices_safe(coins: list[str]) -> dict[str, float | None]:
    """Return ``{coin: price}`` for each coin.

    Priority: scanner ``_ticker_cache`` → CoinDCX tickers API (in thread) → None.
    Never raises; never blocks the event loop.
    """
    if not coins:
        return {}

    import bots.scanner_bot.main as _scanner_main

    def _price_from_tickers(tickers: list, coin: str) -> float | None:
        coin_up = coin.upper()
        for entry in tickers:
            market = str(entry.get("market", ""))
            base   = market.replace("B-", "").split("_")[0].upper()
            if base == coin_up:
                try:
                    return float(entry["last_price"])
                except (KeyError, ValueError, TypeError):
                    pass
        return None

    def _sync_fetch(coins_list: list[str]) -> dict[str, float | None]:
        sc      = getattr(_scanner_main, "_SCANNER", None)
        tickers = getattr(sc, "_ticker_cache", None) if sc is not None else None
        if not tickers:
            try:
                import urllib.request, json as _json
                with urllib.request.urlopen(
                    "https://public.coindcx.com/market_data/ticker", timeout=5
                ) as r:
                    tickers = _json.loads(r.read())
            except Exception:
                tickers = []
        return {c: _price_from_tickers(tickers, c) for c in coins_list}

    try:
        return await asyncio.to_thread(_sync_fetch, list(coins))
    except Exception:
        return {c: None for c in coins}


async def pull_state_payload():

    (watchlist, stats), (mtb_state, pmb_state) = await asyncio.gather(
        asyncio.gather(
            asyncio.to_thread(get_watchlist),
            asyncio.to_thread(get_stats),
        ),
        asyncio.gather(
            _cached_snapshot("mtb", mtb_snapshot),
            _cached_snapshot("pmb", pmb_snapshot),
        ),
    )

    # Enrich closed trade records with pnl_pct, holding_time, entry_price,
    # exit_reason, and current_price.
    # We load the full trade log (not just the snapshot slice) so the buy record
    # that corresponds to each close can always be found.
    try:
        import bots.pmb_bot.storage as _pmb_st
        import bots.mtb_bot.storage as _mtb_st
        _pmb_all, _mtb_all = await asyncio.gather(
            asyncio.to_thread(_pmb_st.load_trades),
            asyncio.to_thread(_mtb_st.load_trades),
        )

        # Collect unique coins across both bots for a single price fetch
        _pmb_closed = pmb_state.get("closed_trades", [])
        _mtb_closed = mtb_state.get("closed_trades", [])
        _coins: set[str] = set()
        for _t in _pmb_closed:
            if _t.get("coin"):
                _coins.add(_t["coin"])
        for _t in _mtb_closed:
            _c = _t.get("coin") or str(_t.get("symbol", "")).replace("B-", "").split("_")[0]
            if _c:
                _coins.add(_c)
        _prices = await _get_current_prices_safe(list(_coins))

        pmb_state = {**pmb_state,
                     "closed_trades": _enrich_closed_trades(
                         _pmb_closed, _pmb_all, prices=_prices)}
        mtb_state = {**mtb_state,
                     "closed_trades": _enrich_closed_trades(
                         _mtb_closed, _mtb_all, prices=_prices)}
    except Exception:
        # NF-3: enrichment is best-effort; raw data still renders fine.
        # Log so operators know which step failed without impacting the page.
        logger.exception("Trade enrichment failed — dashboard will show raw (unenriched) trade data")

    # ── PMB Open Positions — live current price / live PnL ────────────────
    # NOTE: this only decorates the *open* positions list with read-only,
    # display-only fields (current_price / live_pnl / live_pnl_pct /
    # live_pnl_status). It never touches the PMB trading engine, storage
    # files, scanner logic, or trade history — those all remain exactly as
    # loaded from pmb_snapshot() above.
    try:
        _pmb_open = pmb_state.get("open_positions", [])
        _pmb_open_coins = [p.get("coin") for p in _pmb_open if p.get("coin")]
        # Ticker-cache-only lookup — zero CoinDCX API calls, per requirement.
        _pmb_live_prices = await _get_scanner_ticker_prices_only(_pmb_open_coins)
        pmb_state = {**pmb_state,
                     "open_positions": _enrich_open_positions_live_pnl(
                         _pmb_open, _pmb_live_prices)}
    except Exception:
        # NF-3: live-PnL decoration is best-effort; raw positions still render.
        # Log so operators know which step failed without impacting the page.
        logger.exception("PMB open-positions live-PnL enrichment failed — dashboard will show positions without live price decoration")

    vgx_trade_amount = float(os.getenv("VGX_TRADE_AMOUNT", os.getenv("TRADE_AMOUNT", "110")))
    # Read live scan signals from live_signals.json (written each scan cycle by main.py)
    signal_data, latest_market_state, signal_stats = await asyncio.gather(
        asyncio.to_thread(get_live_signals),
        asyncio.to_thread(get_market_state),
        asyncio.to_thread(get_signal_stats),
    )
    latest_signals = signal_data.get("signals", [])[-50:]   # last 50 signals

    # Compute per-tier signal counts from JSON stats (persists across restarts)
    _elite  = signal_stats.get("elite_signals", 0)
    _high   = signal_stats.get("high_signals", 0)
    _medium = signal_stats.get("medium_signals", 0)

    # I-04: Cleanup engine stats from scanner bot in-memory state
    try:
        import bots.scanner_bot.main as _scanner_main
        _cleanup_stats = getattr(_scanner_main, "_CLEANUP_STATS", {})
    except Exception:
        _cleanup_stats = {}

    # I-05 / I-07: Health + recovery stats from scanner bot
    try:
        import bots.scanner_bot.main as _scanner_main
        _health_stats    = getattr(_scanner_main, "_HEALTH_STATS",    {})
        _recovery_stats  = getattr(_scanner_main, "_RECOVERY_STATS",  {})
    except Exception:
        _health_stats   = {}
        _recovery_stats = {}

    def _signal_age(ts_str: str) -> str:
        """Return a human-readable relative age string for a signal timestamp."""
        try:
            from datetime import timezone as _tz
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=_tz.utc)
            diff = int((datetime.now(_tz.utc) - ts).total_seconds())
            if diff < 60:    return f"{diff}s ago"
            if diff < 3600:  return f"{diff // 60}m ago"
            if diff < 86400: return f"{diff // 3600}h ago"
            return f"{diff // 86400}d ago"
        except Exception:
            return "—"

    # Normalise recent_signals: map internal field names to template-expected names
    recent_signals = [
        {
            "coin":         s.get("coin",         ""),
            "category":     s.get("tier",         ""),   # template uses trace.category
            "score":        s.get("score",        0),
            "signal_price": s.get("price",        0),    # template uses trace.signal_price
            "timestamp":    s.get("timestamp",    ""),
            "market_state": s.get("market_state", ""),
            "confidence":   s.get("confidence",   0),
            "coin_class":   s.get("coin_class",   ""),
            "signal_age":   _signal_age(s.get("timestamp", "")),
            "market":       s.get("market", "INR"),    # I-10: INR/USDT market
        }
        for s in latest_signals
    ]

    # ── Portfolio aggregation from live bot snapshots ─────────────────────
    vgx_state = await _cached_snapshot("vgx", vgx_snapshot)

    _vgx_cash       = float(vgx_state.get("virtual_balance", 0))
    _pmb_cash       = float(pmb_state.get("cash_balance", 0))
    _mtb_cash       = float(mtb_state.get("cash_balance", 0))
    _available_cash = round(_vgx_cash + _pmb_cash + _mtb_cash, 2)

    _vgx_invested    = round(sum(float(p.get("amount", 0))         for p in vgx_state.get("open_positions", [])), 2)
    _pmb_invested    = round(sum(float(p.get("total_invested", 0)) for p in pmb_state.get("open_positions", [])), 2)
    _mtb_invested    = round(sum(float(p.get("trade_amount", 0))   for p in mtb_state.get("open_positions", [])), 2)
    _invested_amount = round(_vgx_invested + _pmb_invested + _mtb_invested, 2)

    _total_pnl   = round(float(vgx_state.get("total_pnl", 0)) + float(pmb_state.get("total_pnl", 0)) + float(mtb_state.get("total_pnl", 0)), 2)
    _daily_pnl   = round(float(vgx_state.get("daily_pnl", 0)) + float(pmb_state.get("daily_pnl", 0)) + float(mtb_state.get("daily_pnl", 0)), 2)
    _total_value = round(_available_cash + _invested_amount + _total_pnl, 2)
    _open_pos_count = (len(vgx_state.get("open_positions", [])) +
                       len(pmb_state.get("open_positions", [])) +
                       len(mtb_state.get("open_positions", [])))

    # ── Normalize open positions from all bots into unified schema ─────────
    _all_open_positions: list[dict] = []
    for p in vgx_state.get("open_positions", []):
        _all_open_positions.append({
            "bot":       "VGX",
            "coin":      p.get("coin", ""),
            "quantity":  round(float(p.get("qty", 0)), 8),
            "buy_price": round(float(p.get("buy_price", 0)), 4),
            "pnl_pct":   0,
            "status":    "OPEN",
        })
    for p in pmb_state.get("open_positions", []):
        _all_open_positions.append({
            "bot":       "PMB",
            "coin":      p.get("coin", ""),
            "quantity":  round(float(p.get("total_quantity", 0)), 8),
            "buy_price": round(float(p.get("avg_entry_price", 0)), 4),
            "pnl_pct":   0,
            "status":    p.get("status", "OPEN"),
        })
    for p in mtb_state.get("open_positions", []):
        _all_open_positions.append({
            "bot":       "MTB",
            "coin":      p.get("coin", p.get("symbol", "")),
            "quantity":  round(float(p.get("quantity", 0)), 8),
            "buy_price": round(float(p.get("entry_price", p.get("buy_price", 0))), 4),
            "pnl_pct":   0,   # no current price in snapshot; live pnl_pct not available
            "status":    p.get("status", "OPEN"),
        })

    _scanned_wl, _coin_pairs = await asyncio.gather(
        asyncio.to_thread(_scanner_get_watchlist),
        asyncio.to_thread(_build_coin_pairs),
    )

    return {

        "portfolio_overview": {
            "total_value":    _total_value,
            "daily_pnl":      _daily_pnl,
            "available_cash": _available_cash,
            "invested_amount": _invested_amount,
            "total_pnl":      _total_pnl,
            "open_positions": _open_pos_count,
        },

        "mtb_status": mtb_state["status"],
        "mtb_open_positions": mtb_state["open_positions"],
        "mtb_closed_trades": mtb_state["closed_trades"],
        "mtb_daily_pnl": mtb_state["daily_pnl"],
        "mtb_trade_amount": mtb_state["trade_amount"],
        "mtb_overview": mtb_state,
        "vgx_overview":  vgx_state,
        "pmb_overview": pmb_state,
        "risk_engine":  await _cached_snapshot("risk", risk_snapshot),
        "vgx_trade_amount": vgx_trade_amount,

        "scanner_overview": {
            "coins":           _scanned_wl.get("coins", []),
            "coin_pairs":      _coin_pairs,
            "coins_scanned":   len(_scanned_wl.get("coins", [])),
            "active_signals":  len(latest_signals),
            "elite_signals":   _elite,
            "high_signals":    _high,
            "medium_signals":  _medium,
            "market_state":    "ACTIVE",
            "last_scan_time":  "LIVE",
            # I-04: Cleanup engine stats
            "expired_signals":    _cleanup_stats.get("expired_last_run", 0),
            "last_cleanup_time":  _cleanup_stats.get("last_cleanup_time"),
            "next_cleanup_time":  _cleanup_stats.get("next_cleanup_time"),
            "total_expired":      _cleanup_stats.get("total_expired_lifetime", 0),
            # I-05: Scanner health monitor
            "api_status":             _health_stats.get("api_status", "ONLINE"),
            "last_successful_scan":   _health_stats.get("last_successful_scan"),
            "total_scans":            _health_stats.get("total_scans", 0),
            "failed_scans":           _health_stats.get("failed_scans", 0),
            "consecutive_failures":   _health_stats.get("consecutive_failures", 0),
            "scan_duration_ms":       _health_stats.get("scan_duration_ms", 0),
            "current_market_status":  _health_stats.get("current_market_status", ""),
            "health_score":           _health_stats.get("health_score", 100),
            "health_color":           _health_stats.get("health_color", "green"),
            # I-07: Restart recovery
            "last_restart_time":      _recovery_stats.get("last_restart_time"),
            "recovered_signals":      _recovery_stats.get("recovered_signals", 0),
            "recovery_status":        _recovery_stats.get("recovery_status", "SUCCESS"),
        },

        "service_statuses": {
            "scanner": (
                "ONLINE"
                if getattr(scanner_main, "_SCANNER_TASK", None)
                and not getattr(scanner_main, "_SCANNER_TASK").done()
                else "OFFLINE"
            ),
            "vgx": (await _cached_snapshot("vgx", vgx_snapshot)).get("status", "OFFLINE"),
            "mtb": (
                "ONLINE"
                if getattr(mtb_main, "_MTB_TASK", None)
                and not getattr(mtb_main, "_MTB_TASK").done()
                else "OFFLINE"
            ),
            "pmb": (
                "ONLINE"
                if getattr(pmb_main, "_PMB_TASK", None)
                and not getattr(pmb_main, "_PMB_TASK").done()
                else "OFFLINE"
            ),
            "scanner_telegram": (
                "ONLINE" if getattr(scanner_tg, "_SCANNER_TG_APP", None) is not None
                else "OFFLINE"
            ),
            "vgx_telegram": (
                "ONLINE" if getattr(vgx_tg, "_VGX_TG_APP", None) is not None
                else "OFFLINE"
            ),
            "pmb_telegram": (
                "ONLINE" if getattr(pmb_tg, "_PMB_TG_APP", None) is not None
                else "OFFLINE"
            ),
            "mtb_telegram": (
                "ONLINE" if getattr(mtb_tg, "_MTB_TG_APP", None) is not None
                else "OFFLINE"
            ),
        },

        "railway_monitoring": {
            "status":        "ACTIVE",
            "cpu_usage":     _get_cpu_usage(),
            "memory_usage":  _get_memory_usage(),
            "restart_count": int(os.getenv("RAILWAY_RESTART_COUNT", "0")),
        },

        "system_meta": {
            "uptime":             _get_uptime(),
            "version":            "v1.0",
            "environment":        os.getenv("RAILWAY_ENVIRONMENT", "PRODUCTION"),
            "overall_health_pct": _get_health_pct(),
        },

        "recent_signals": recent_signals,

        "market_state": {
            **latest_market_state,
            "market_strength": latest_market_state.get("strength", _compute_market_strength(latest_signals)),
        },

        "charts": _build_charts_payload(latest_signals),

        "activity_timeline": [],
        "open_positions":    _all_open_positions,
        "closed_trades":     [],

        "watchlist":       watchlist,
        "stats":           stats,
        "notifications":   [],
        "error_logs":      [],
        "performance_stats": get_performance_stats(),
        "performance_signals": get_performance_signals(),
        "coin_performance": {
            "BTC": get_per_coin_performance("BTC"),
            "ETH": get_per_coin_performance("ETH"),
            "SOL": get_per_coin_performance("SOL"),
            "XRP": get_per_coin_performance("XRP"),
        },
        "signal_history": get_signal_history(),
        "signal_history_stats": get_signal_history_stats(),
        "coin_performance_data": get_coin_performance_data(),
        "coin_performance_stats": get_coin_performance_stats(),
        "tier_accuracy_data": get_tier_accuracy_data(),
        "tier_accuracy_stats": get_tier_accuracy_stats(),
        "scanner_meta": {
            "last_scan_time": getattr(scanner_main, "_LAST_SCAN_TIME", None),
            "scan_cycles": getattr(scanner_main, "_SCAN_CYCLES", 0),
            "signals_generated": getattr(scanner_main, "_SIGNALS_GENERATED", 0),
            "status": "RUNNING" if getattr(scanner_main, "_SCAN_CYCLES", 0) > 0 else "STOPPED",
            "next_scan_in": 300,
        },
    }
  
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Show the login form. Redirects to dashboard if already authenticated."""
    if request.session.get("authenticated"):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"request": request, "error": None},
    )


# ── Failed-login throttle ─────────────────────────────────────────────────
# In-memory only; cleared on server restart. Never touches API auth or bots.
_FAILED_LOGINS: dict[str, dict] = {}  # {ip: {"attempts": int, "locked_until": float}}

_MAX_ATTEMPTS  = 5          # failures before lockout
_LOCKOUT_SECS  = 300        # 5-minute lockout window


def _get_client_ip(request: Request) -> str:
    """Return the direct TCP peer address.

    X-Forwarded-For is intentionally ignored: it is client-controlled and
    trusting it would let any caller rotate through spoofed IPs to bypass
    the lockout.  If a trusted reverse proxy is added in future, gate on
    a known proxy CIDR before accepting the forwarded header.
    """
    return request.client.host if request.client else "unknown"


def _is_locked(ip: str) -> tuple[bool, float]:
    """Return (locked, seconds_remaining). Cleans up expired lockouts only."""
    record = _FAILED_LOGINS.get(ip)
    if not record:
        return False, 0.0
    if not record["locked_until"]:
        # Failures are accumulating but lockout threshold not yet reached.
        # Do NOT remove the record — that would reset the counter.
        return False, 0.0
    if _time.time() < record["locked_until"]:
        remaining = record["locked_until"] - _time.time()
        return True, remaining
    # Lockout window has expired — clean up and allow retry
    _FAILED_LOGINS.pop(ip, None)
    return False, 0.0


def _record_failure(ip: str) -> None:
    """Increment failure counter; engage lockout when threshold is reached."""
    record = _FAILED_LOGINS.setdefault(ip, {"attempts": 0, "locked_until": 0.0})
    record["attempts"] += 1
    if record["attempts"] >= _MAX_ATTEMPTS:
        record["locked_until"] = _time.time() + _LOCKOUT_SECS


def _clear_failures(ip: str) -> None:
    """Remove failure record on successful authentication."""
    _FAILED_LOGINS.pop(ip, None)


@app.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request, api_key: str = Form(...)):
    """Validate the API key and set a session cookie on success."""
    ip = _get_client_ip(request)

    # Throttle check — runs before any key comparison
    locked, remaining = _is_locked(ip)
    if locked:
        minutes = int(remaining // 60) + 1
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "request": request,
                "error": (
                    f"Too many failed login attempts. "
                    f"Please try again in {minutes} minute{'s' if minutes != 1 else ''}."
                ),
            },
        )

    if DASHBOARD_API_KEY and hmac.compare_digest(api_key, DASHBOARD_API_KEY):
        _clear_failures(ip)
        request.session["authenticated"] = True
        return RedirectResponse(url="/", status_code=303)

    _record_failure(ip)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"request": request, "error": "Invalid API key — please try again."},
    )


@app.get("/logout", response_class=HTMLResponse)
async def logout(request: Request):
    """Clear the session and redirect to the login page."""
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
async def viewport_router(request: Request):
    # Session gate — unauthenticated browsers are redirected to /login.
    # The API key is only injected into the page after this check passes,
    # so it is never reachable by unauthenticated visitors.
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/login", status_code=303)

    state, signals, stats, watchlist = await asyncio.gather(
        pull_state_payload(),
        asyncio.to_thread(get_signals),
        asyncio.to_thread(get_stats),
        asyncio.to_thread(get_watchlist),
    )


    return templates.TemplateResponse(
        request=request,

        name="dashboard.html",
        context={
            "request": request,
            "data": state,
            "signals": signals,
            "stats": stats,
            "watchlist": watchlist,
            "dashboard_api_key": DASHBOARD_API_KEY or "",
        }
    )
   
# ──────────────────────────────────────────────────────────────
# Watchlist Center API
# ──────────────────────────────────────────────────────────────

from pydantic import BaseModel

class WatchlistRequest(BaseModel):
    coin: str

async def _fetch_tickers() -> list:
    """Return the raw ticker list from the scanner's in-memory cache, falling
    back to a fresh CoinDCX API call when the cache is not yet warm.

    This is the single canonical ticker-fetch path used by both
    _get_coin_markets() and add_coin_to_scanner_watchlist() so pair-resolution
    logic cannot drift between the two callers.

    Returns [] on any network/API failure so callers can treat an empty list
    as "cache not available" rather than a hard error.
    """
    import bots.scanner_bot.main as _sm
    sc = getattr(_sm, "_SCANNER", None)
    cached = getattr(sc, "_ticker_cache", None) if sc is not None else None
    if cached:
        return list(cached)
    try:
        import requests as _req
        resp = await asyncio.to_thread(
            _req.get,
            "https://api.coindcx.com/exchange/ticker",
            timeout=6,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return []


async def _get_coin_markets() -> tuple:
    """Return (inr_coins, usdt_coins) — two separate sets of base symbols.

    INR set: coins that have a direct COIN+INR pair on CoinDCX.
    USDT set: coins that have a COIN+USDT pair but no INR pair.

    Uses _fetch_tickers() (single canonical path) so pair-resolution logic
    stays consistent with add_coin_to_scanner_watchlist().
    """
    tickers = await _fetch_tickers()
    if not tickers:
        return set(), set()
    inr_coins: set = set()
    usdt_coins: set = set()
    for ticker in tickers:
        market = ticker.get("market", "")
        if market.endswith("INR") and len(market) > 3:
            inr_coins.add(market[:-3])
        elif market.endswith("USDT") and len(market) > 4:
            usdt_coins.add(market[:-4])
    return inr_coins, usdt_coins


async def _get_supported_coins() -> set:
    """Return the union of INR + USDT coins. Used by /api/supported-coins."""
    inr, usdt = await _get_coin_markets()
    return inr | usdt


def _build_coin_pairs() -> list[dict]:
    """Return [{coin, pair, quote}] for the dashboard server-side render.

    Uses the scanner's in-memory store + ticker cache for lazy resolution.
    Falls back gracefully when the scanner has not yet started.
    Always returns a list — never raises.
    """
    try:
        sc = getattr(scanner_main, "_SCANNER", None)
        if sc is not None:
            store = sc.watchlist_store
            items = store.all_with_pairs()
            tickers: list = getattr(sc, "_ticker_cache", None) or []
            for item in items:
                if item["pair"] is None and tickers:
                    resolved = _resolve_coin_pair(item["coin"], tickers=tickers)
                    if resolved["resolved"]:
                        item["pair"]  = resolved["pair"]
                        item["quote"] = resolved["quote"]
                        store.set_pair(item["coin"], resolved["pair"], resolved["quote"])
            return items
    except Exception:
        pass
    coins = _scanner_get_watchlist().get("coins", [])
    return [{"coin": c, "pair": None, "quote": None} for c in coins]


# I-08: Manual refresh endpoint
@app.get("/api/watchlist", response_class=JSONResponse)
async def get_scanner_watchlist():
    """Return the unified scanner watchlist with resolved pair metadata.

    Response includes:
      coins — list[str]  — backward-compatible bare symbol list
      items — list[dict] — [{coin, pair, quote}] with lazy pair resolution
    """
    try:
        sc = getattr(scanner_main, "_SCANNER", None)
        if sc is not None:
            store = sc.watchlist_store
            items = store.all_with_pairs()
            tickers: list = getattr(sc, "_ticker_cache", None) or []
            for item in items:
                if item["pair"] is None and tickers:
                    resolved = _resolve_coin_pair(item["coin"], tickers=tickers)
                    if resolved["resolved"]:
                        item["pair"]  = resolved["pair"]
                        item["quote"] = resolved["quote"]
                        store.set_pair(item["coin"], resolved["pair"], resolved["quote"])
            coins = [i["coin"] for i in items]
            return {"count": len(coins), "coins": coins, "items": items}
    except Exception:
        pass
    raw = await asyncio.to_thread(_scanner_get_watchlist)
    coins = raw.get("coins", [])
    items = [{"coin": c, "pair": None, "quote": None} for c in coins]
    return {"count": len(coins), "coins": coins, "items": items}


@app.post("/api/watchlist/add", response_class=JSONResponse)
async def add_coin_to_scanner_watchlist(req: WatchlistRequest):
    """Add a coin to the unified scanner watchlist.

    Delegates to watchlist_ops.add_coin() — the single canonical
    implementation shared with /api/v1/scanner/watchlist (POST).
    """
    try:
        result = await _watchlist_ops.add_coin(req.coin)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    if not result["ok"]:
        return JSONResponse(
            {
                "success": False,
                "reason":  result["reason"],
                "error":   result["error"],
                "coin":    result["coin"],
            },
            status_code=400,
        )

    return {
        "success":  True,
        "coin":     result["coin"],
        "pair":     result["pair"],
        "quote":    result["quote"],
        "market":   result["market"],
        "watchlist": result["watchlist"],
    }


@app.post("/api/watchlist/remove", response_class=JSONResponse)
async def remove_coin_from_scanner_watchlist(req: WatchlistRequest):
    """Remove a coin from the unified scanner watchlist.

    Delegates to watchlist_ops.remove_coin() — the single canonical
    implementation shared with /api/v1/scanner/watchlist (DELETE).
    """
    try:
        result = await _watchlist_ops.remove_coin(req.coin)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    return {
        "success":  result["ok"],
        "coin":     result["coin"],
        "watchlist": result["watchlist"],
    }


@app.post("/api/scanner/refresh", response_class=JSONResponse)
async def refresh_scanner():
    """Trigger an immediate scanner refresh from the dashboard.
    Directly sets the scanner's refresh event (same process)."""
    try:
        import bots.scanner_bot.main as _scanner_main
        _scanner_main._REFRESH_EVENT.set()
        return JSONResponse(content={"success": True, "message": "Scan triggered"})
    except Exception as e:
        return JSONResponse(
            content={"success": False, "error": "Scanner refresh failed"},
            status_code=503,
        )


class TradingToggleRequest(BaseModel):
    enabled: bool


@app.get("/api/v1/trading/status", response_class=JSONResponse)
async def trading_status():
    """Return the live in-memory trading toggle state."""
    global _trading_changed_by, _trading_changed_at
    with _trading_meta_lock:
        changed_by = _trading_changed_by
        changed_at = _trading_changed_at
    return {
        "trading_enabled": _get_trading_enabled(),
        "emergency_stop":  _EMERGENCY_STOP,
        "changed_by":      changed_by,
        "changed_at":      changed_at,
    }


@app.post("/api/v1/trading/toggle", response_class=JSONResponse)
async def trading_toggle(req: TradingToggleRequest):
    """Enable or disable the global TRADING_ENABLED kill-switch from the
    dashboard.  In-memory only — reverts to the env-var default on restart.
    Rejected when EMERGENCY_STOP is active."""
    global _trading_changed_by, _trading_changed_at
    if req.enabled and _EMERGENCY_STOP:
        return JSONResponse(
            {"status": "rejected", "reason": "EMERGENCY_STOP is active"},
            status_code=200,
        )
    now = datetime.now(timezone.utc).isoformat()
    effective = _set_trading_enabled_imem(req.enabled)
    with _trading_meta_lock:
        _trading_changed_by = "dashboard"
        _trading_changed_at = now
    logger.info("[Trading] TRADING_ENABLED set to %s by dashboard", effective)
    return {"status": "ok", "trading_enabled": effective, "changed_at": now}


@app.post("/api/risk/toggle-trading", response_class=JSONResponse)
async def toggle_trading(req: TradingToggleRequest):
    """Enable or disable the global TRADING_ENABLED kill-switch from the
    dashboard. Persists the override to disk (survives restarts) without
    requiring an env var change; does not affect the separate EMERGENCY_STOP
    switch or existing open positions."""
    try:
        effective = _risk_set_trading_enabled(req.enabled)
        return {"success": True, "trading_enabled": effective}
    except Exception as e:
        return JSONResponse(
            {"success": False, "error": str(e)},
            status_code=500,
        )




@app.get("/api/supported-coins", response_class=JSONResponse)
async def supported_coins_endpoint():
    """Return sorted list of coin symbols that have an INR or USDT pair on CoinDCX."""
    coins = sorted(await _get_supported_coins())
    return {"coins": coins}


@app.get("/api/v1/state", response_class=JSONResponse)
async def unified_state_polling_endpoint():
    """Future production data hook. Live bots simply post metrics to rewrite state."""
    return await pull_state_payload()


@app.get("/api/v1/prices", response_class=JSONResponse)
async def get_live_prices():
    """Return ``{prices: {COIN: float}}`` from the scanner's in-memory ticker cache.

    Zero CoinDCX API calls — reads only ``_SCANNER._ticker_cache``.
    Used by the dashboard JS to patch trade history Cur. Price cells every refresh cycle.
    """
    import bots.scanner_bot.main as _scanner_main

    def _read_cache() -> dict[str, float]:
        sc      = getattr(_scanner_main, "_SCANNER", None)
        tickers = getattr(sc, "_ticker_cache", None) if sc is not None else None
        if not tickers:
            return {}
        prices: dict[str, float] = {}
        for entry in tickers:
            market = str(entry.get("market", ""))
            base   = market.replace("B-", "").split("_")[0].upper()
            if not base:
                continue
            try:
                price = float(entry["last_price"])
            except (KeyError, ValueError, TypeError):
                continue
            if base not in prices:   # first match wins (INR before USDT)
                prices[base] = price
        return prices

    try:
        prices = await asyncio.to_thread(_read_cache)
    except Exception:
        prices = {}
    return {"prices": prices, "count": len(prices)}

# ═══════════════════════════════════════════════════════════════
#  UNIFIED STATISTICS ENGINE
# ═══════════════════════════════════════════════════════════════

def _unified_stats(
    vgx: dict | None = None,
    mtbs: dict | None = None,
    pmbs: dict | None = None,
) -> dict:
    """
    Single source of truth for all analytics across VGX, PMB, MTB.
    Win rate, best/worst signal, coin leaderboard — computed fresh each call.
    Accepts pre-fetched snapshot dicts so async callers can supply them via
    asyncio.to_thread instead of blocking here.
    """
    from bots.scanner_bot.scanner import get_signals as _get_signals

    raw_signals: list = []
    try:
        data = _get_signals()
        raw_signals = data.get("signals", []) if isinstance(data, dict) else []
    except Exception:
        pass

    wins = losses = 0
    best_signal = worst_signal = None
    best_ret = worst_ret = None
    coin_stats: dict = {}

    for sig in raw_signals:
        coin  = sig.get("coin", "UNKNOWN")
        evals = sig.get("evaluations") or {}
        ret   = None
        for h in ("24h", "4h", "1h"):
            ev = evals.get(h)
            if ev:
                try:
                    ret = float(ev["change_percent"]); break
                except (KeyError, TypeError, ValueError):
                    pass
        if ret is None:
            continue
        if ret > 0:
            wins += 1
        else:
            losses += 1
        if best_ret is None or ret > best_ret:
            best_ret = ret
            best_signal = {"coin": coin, "return": round(ret, 4), "tier": sig.get("priority", ""), "timestamp": sig.get("timestamp", "")}
        if worst_ret is None or ret < worst_ret:
            worst_ret = ret
            worst_signal = {"coin": coin, "return": round(ret, 4), "tier": sig.get("priority", ""), "timestamp": sig.get("timestamp", "")}
        if coin not in coin_stats:
            coin_stats[coin] = {"coin": coin, "signals": 0, "wins": 0, "losses": 0, "total_return": 0.0}
        coin_stats[coin]["signals"] += 1
        coin_stats[coin]["total_return"] = round(coin_stats[coin]["total_return"] + ret, 4)
        if ret > 0:
            coin_stats[coin]["wins"] += 1
        else:
            coin_stats[coin]["losses"] += 1

    evaluated = wins + losses
    win_rate  = round(wins / evaluated * 100, 2) if evaluated else 0.0
    leaderboard = sorted(coin_stats.values(), key=lambda x: x["total_return"], reverse=True)
    for entry in leaderboard:
        n = entry["wins"] + entry["losses"]
        entry["win_rate"] = round(entry["wins"] / n * 100, 1) if n else 0.0

    if vgx is None:
        vgx  = vgx_snapshot()
    if mtbs is None:
        mtbs = mtb_snapshot()
    if pmbs is None:
        pmbs = pmb_snapshot()

    return {
        "signals_total":     len(raw_signals),
        "signals_evaluated": evaluated,
        "win_rate":          win_rate,
        "wins":              wins,
        "losses":            losses,
        "best_signal":       best_signal,
        "worst_signal":      worst_signal,
        "coin_leaderboard":  leaderboard[:20],
        "bot_pnl": {
            "vgx": {"daily_pnl": vgx.get("daily_pnl", 0),  "total_pnl": vgx.get("total_pnl", 0)},
            "mtb": {"daily_pnl": mtbs.get("daily_pnl", 0), "total_pnl": mtbs.get("total_pnl", 0)},
            "pmb": {"daily_pnl": pmbs.get("daily_pnl", 0), "total_pnl": pmbs.get("total_pnl", 0)},
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/v1/stats/unified", response_class=JSONResponse)
async def unified_statistics():
    """Unified statistics engine — win rate, best/worst signal, coin leaderboard."""
    try:
        vgx, mtbs, pmbs = await asyncio.gather(
            _cached_snapshot("vgx", vgx_snapshot),
            _cached_snapshot("mtb", mtb_snapshot),
            _cached_snapshot("pmb", pmb_snapshot),
        )
        return await asyncio.to_thread(_unified_stats, vgx, mtbs, pmbs)
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


@app.get("/api/v1/stats/leaderboard", response_class=JSONResponse)
async def coin_leaderboard():
    """Coin leaderboard sorted by total return across all evaluated signals."""
    try:
        # Pre-fetch snapshots via the cache (uses asyncio.to_thread internally) so
        # _unified_stats never falls back to its own synchronous file-I/O path.
        vgx, mtbs, pmbs = await asyncio.gather(
            _cached_snapshot("vgx", vgx_snapshot),
            _cached_snapshot("mtb", mtb_snapshot),
            _cached_snapshot("pmb", pmb_snapshot),
        )
        logger.debug("[dashboard] offloading _unified_stats (leaderboard) to thread")
        stats = await asyncio.to_thread(_unified_stats, vgx, mtbs, pmbs)
        return {"leaderboard": stats["coin_leaderboard"], "timestamp": stats["timestamp"]}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


# ═══════════════════════════════════════════════════════════════
#  PHASE 7 — V1 FREEZE: 14-DAY PAPER TRADING VALIDATION STATUS
# ═══════════════════════════════════════════════════════════════

@app.get("/api/v1/validation/status", response_class=JSONResponse)
async def paper_trading_validation_status():
    """Phase 7 — V1 Freeze: 14-day paper trading validation tracker.

    Set PAPER_TRADING_START to an ISO-8601 UTC datetime (e.g. 2026-07-01T00:00:00Z)
    to begin counting the validation window.  Before that env var is set,
    days_elapsed / days_remaining are null and validation_complete is false.
    """
    try:
        now = datetime.now(timezone.utc)
        validation_period_days = 14

        # ── Parse start date ────────────────────────────────────────────────
        start_str = os.getenv("PAPER_TRADING_START")
        start_dt: datetime | None = None
        if start_str:
            try:
                start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                if start_dt.tzinfo is None:
                    start_dt = start_dt.replace(tzinfo=timezone.utc)
            except ValueError:
                start_dt = None

        if start_dt is not None:
            # Clamp to 0 so a future start_date never yields negative elapsed days.
            elapsed_s      = max(0.0, (now - start_dt).total_seconds())
            days_elapsed   = round(elapsed_s / 86400, 2)
            days_remaining = round(max(0.0, validation_period_days - days_elapsed), 2)
            is_complete    = days_elapsed >= validation_period_days
        else:
            days_elapsed = days_remaining = None
            is_complete  = False

        # ── Bot snapshots ───────────────────────────────────────────────────
        vgx, mtbs, pmbs = await asyncio.gather(
            _cached_snapshot("vgx", vgx_snapshot),
            _cached_snapshot("mtb", mtb_snapshot),
            _cached_snapshot("pmb", pmb_snapshot),
        )

        vgx_mode = str(vgx.get("status", "UNKNOWN")).upper()
        mtb_mode = str(mtbs.get("mode",   "UNKNOWN")).upper()
        pmb_mode = str(pmbs.get("mode",   "UNKNOWN")).upper()
        all_paper = all(m == "PAPER" for m in (vgx_mode, mtb_mode, pmb_mode))

        # ── Circuit-breaker state (read persisted JSON; no live instance) ───
        cb_state  = "UNKNOWN"
        cb_breaks = 0
        try:
            from bots.volatile_gridX.circuit_breaker import CIRCUIT_BREAKER_FILE
            import json as _json
            if CIRCUIT_BREAKER_FILE.exists():
                def _read_cb_file():
                    with open(CIRCUIT_BREAKER_FILE) as _f:
                        return _json.load(_f)
                _cb = await asyncio.to_thread(_read_cb_file)
                cb_state  = _cb.get("trading_state", "UNKNOWN")
                cb_breaks = int(_cb.get("circuit_breaks_count", 0))
            else:
                cb_state = "ACTIVE"
        except Exception:
            # NF-4: circuit breaker file read failed — status panel shows stale/default values.
            logger.exception("Circuit breaker file read failed — cb_state will remain at default")

        return {
            "phase":                   "Phase 7 — V1 Freeze",
            "validation_period_days":  validation_period_days,
            "start_date":              start_str or None,
            "days_elapsed":            days_elapsed,
            "days_remaining":          days_remaining,
            "validation_complete":     is_complete,
            "all_bots_in_paper_mode":  all_paper,
            "bots": {
                "vgx": {
                    "mode":           vgx_mode,
                    "daily_pnl":      vgx.get("daily_pnl",    0),
                    "total_pnl":      vgx.get("total_pnl",    0),
                    "open_positions": len(vgx.get("open_positions", [])),
                    "paper_trades":   vgx.get("paper_trades",  0),
                    "win_rate":       vgx.get("win_rate",       0),
                },
                "mtb": {
                    "mode":           mtb_mode,
                    "daily_pnl":      mtbs.get("daily_pnl",    0),
                    "total_pnl":      mtbs.get("total_pnl",    0),
                    "open_positions": len(mtbs.get("open_positions", [])),
                    "closed_trades":  len(mtbs.get("closed_trades",  [])),
                    "cash_balance":   mtbs.get("cash_balance",  0),
                },
                "pmb": {
                    "mode":           pmb_mode,
                    "daily_pnl":      pmbs.get("daily_pnl",    0),
                    "total_pnl":      pmbs.get("total_pnl",    0),
                    "open_positions": len(pmbs.get("open_positions", [])),
                    "closed_trades":  len(pmbs.get("closed_trades",  [])),
                    "cash_balance":   pmbs.get("cash_balance",  0),
                },
            },
            "circuit_breaker": {
                "state":        cb_state,
                "total_breaks": cb_breaks,
            },
            "timestamp": now.isoformat(),
        }
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


# ═══════════════════════════════════════════════════════════════
#  ALERT NOTIFICATION CENTER
# ═══════════════════════════════════════════════════════════════

_ALERT_LOG: list = []
_ALERT_LOG_MAX = 200
_ALERT_LOG_LOCK = asyncio.Lock()


async def _push_alert(level: str, source: str, message: str) -> None:
    async with _ALERT_LOG_LOCK:
        _ALERT_LOG.append({
            "level":     level,
            "source":    source,
            "message":   message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        if len(_ALERT_LOG) > _ALERT_LOG_MAX:
            del _ALERT_LOG[: len(_ALERT_LOG) - _ALERT_LOG_MAX]


@app.get("/api/v1/alerts", response_class=JSONResponse)
async def alert_center(limit: int = 50):
    """Alert Notification Center — most recent alerts, newest first."""
    alerts = list(reversed(_ALERT_LOG[-limit:]))
    return {"alerts": alerts, "total": len(_ALERT_LOG), "limit": limit}


class AlertPushRequest(BaseModel):
    title:    str = "system"
    message:  str = ""
    severity: str = "INFO"


@app.post("/api/v1/alerts/push", response_class=JSONResponse)
async def push_alert(body: AlertPushRequest):
    """Push a new alert into the notification center.

    Accepts a JSON body:
      { "title": "...", "message": "...", "severity": "INFO" }

    title    maps to the internal 'source' field.
    severity maps to the internal 'level' field.
    """
    await _push_alert(body.severity.upper(), body.title, body.message)
    return {"ok": True, "total": len(_ALERT_LOG)}


# ═══════════════════════════════════════════════════════════════
#  ERROR LOG VIEWER
# ═══════════════════════════════════════════════════════════════

_ERROR_LOG: list = []
_ERROR_LOG_MAX = 100
_ERROR_LOG_LOCK = asyncio.Lock()


async def _log_error(source: str, error: str, context: str = "") -> None:
    async with _ERROR_LOG_LOCK:
        _ERROR_LOG.append({
            "source":    source,
            "error":     error,
            "context":   context,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        if len(_ERROR_LOG) > _ERROR_LOG_MAX:
            del _ERROR_LOG[: len(_ERROR_LOG) - _ERROR_LOG_MAX]


@app.get("/api/v1/errors", response_class=JSONResponse)
async def error_log_viewer(limit: int = 50):
    """Error Log Viewer — most recent errors, newest first."""
    errors = list(reversed(_ERROR_LOG[-limit:]))
    return {"errors": errors, "total": len(_ERROR_LOG), "limit": limit}


# ═══════════════════════════════════════════════════════════════
#  TELEGRAM ANALYTICS
# ═══════════════════════════════════════════════════════════════

@app.get("/api/v1/stats/telegram", response_class=JSONResponse)
async def telegram_analytics():
    """Telegram-ready analytics summary — compact format for bot /stats command."""
    try:
        risk, vgx, mtbs, pmbs = await asyncio.gather(
            _cached_snapshot("risk", risk_snapshot),
            _cached_snapshot("vgx",  vgx_snapshot),
            _cached_snapshot("mtb",  mtb_snapshot),
            _cached_snapshot("pmb",  pmb_snapshot),
        )
        stats = await asyncio.to_thread(_unified_stats, vgx, mtbs, pmbs)

        lines = [
            "📊 PROJECT-ALPHA ANALYTICS",
            "",
            "📡 Scanner",
            f"  Signals: {stats['signals_total']}  Evaluated: {stats['signals_evaluated']}",
            f"  Win Rate: {stats['win_rate']}%  (W:{stats['wins']} L:{stats['losses']})",
        ]
        if stats["best_signal"]:
            b = stats["best_signal"]
            lines.append(f"  Best: {b['coin']} +{b['return']}%")
        if stats["worst_signal"]:
            w = stats["worst_signal"]
            lines.append(f"  Worst: {w['coin']} {w['return']}%")
        lines += [
            "",
            f"🤖 VGX  [{vgx.get('status','?')}]",
            f"  Daily PnL: ₹{vgx.get('daily_pnl', 0):.2f}  Total: ₹{vgx.get('total_pnl', 0):.2f}",
            f"  Positions: {len(vgx.get('open_positions', []))}  Win Rate: {vgx.get('win_rate', 0)}%",
            "",
            f"🤖 MTB  [{mtbs.get('status','?')}]",
            f"  Daily PnL: ₹{mtbs.get('daily_pnl', 0):.4f}  Cash: ₹{mtbs.get('cash_balance', 0):.2f}",
            f"  Positions: {len(mtbs.get('open_positions', []))}",
            "",
            f"🤖 PMB  [{pmbs.get('status','?')}]",
            f"  Daily PnL: ₹{pmbs.get('daily_pnl', 0):.4f}  Cash: ₹{pmbs.get('cash_balance', 0):.2f}",
            f"  Positions: {len(pmbs.get('open_positions', []))}",
            "",
            "⚡ Risk Engine",
            f"  Trading: {'✅' if risk.get('trading_enabled') else '❌'}  Emergency Stop: {'🔴' if risk.get('emergency_stop') else '✅'}",
            f"  Capital: ₹{risk.get('total_deployed', 0):.2f} / ₹{risk.get('total_capital_limit', 0):.0f}  ({risk.get('capital_utilisation_pct', 0):.1f}%)",
        ]

        return {
            "text": "\n".join(lines),
            "stats": stats,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        await _log_error("telegram_analytics", str(exc))
        return JSONResponse(status_code=500, content={"error": str(exc)})


# ═══════════════════════════════════════════════════════════════
#  CSV / JSON EXPORT
# ═══════════════════════════════════════════════════════════════

from fastapi.responses import StreamingResponse
import csv
import io


@app.get("/api/v1/export/signals.json")
async def export_signals_json():
    """Download all scanner signals as JSON."""
    try:
        import json as _json
        from bots.scanner_bot.scanner import get_signals as _gs
        _sig_data = await asyncio.to_thread(_gs)
        content = _json.dumps(_sig_data, indent=2, ensure_ascii=False)
        return StreamingResponse(
            io.BytesIO(content.encode("utf-8")),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=signals.json"},
        )
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


@app.get("/api/v1/export/signals.csv")
async def export_signals_csv():
    """Download all scanner signals as CSV."""
    try:
        from bots.scanner_bot.scanner import get_signals as _gs
        data = await asyncio.to_thread(_gs)
        signals = data.get("signals", []) if isinstance(data, dict) else []
        output = io.StringIO()
        fieldnames = ["coin", "priority", "market_state", "opportunity_score",
                      "opp_confidence", "risk_level", "timestamp", "eval_1h", "eval_4h", "eval_24h"]
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for sig in signals:
            evals = sig.get("evaluations") or {}
            writer.writerow({
                "coin":              sig.get("coin", ""),
                "priority":          sig.get("priority", ""),
                "market_state":      sig.get("market_state", ""),
                "opportunity_score": sig.get("opportunity_score", 0),
                "opp_confidence":    sig.get("opp_confidence", 0),
                "risk_level":        sig.get("risk_level", ""),
                "timestamp":         sig.get("timestamp", ""),
                "eval_1h":           evals.get("1h", {}).get("change_percent", ""),
                "eval_4h":           evals.get("4h", {}).get("change_percent", ""),
                "eval_24h":          evals.get("24h", {}).get("change_percent", ""),
            })
        return StreamingResponse(
            io.BytesIO(output.getvalue().encode("utf-8")),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=signals.csv"},
        )
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


@app.get("/api/v1/export/trades.csv")
async def export_trades_csv():
    """Download all trades from VGX, MTB, PMB as unified CSV."""
    try:
        from bots.mtb_bot.storage import load_trades as mtb_trades
        from bots.pmb_bot.storage import load_trades as pmb_trades
        output = io.StringIO()
        fieldnames = ["bot", "coin", "symbol", "action", "status", "price", "amount", "pnl", "timestamp"]
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        vgx_raw = await _cached_snapshot("vgx", vgx_snapshot)
        for t in vgx_raw.get("open_positions", []):
            writer.writerow({"bot": "VGX", "coin": t.get("coin"), "action": "BUY",
                             "status": "OPEN", "price": t.get("buy_price"), "amount": t.get("amount")})
        _mtb_rows, _pmb_rows = await asyncio.gather(
            asyncio.to_thread(mtb_trades),
            asyncio.to_thread(pmb_trades),
        )
        for trade in _mtb_rows:
            row = dict(trade); row["bot"] = "MTB"; writer.writerow(row)
        for trade in _pmb_rows:
            row = dict(trade); row["bot"] = "PMB"; writer.writerow(row)
        return StreamingResponse(
            io.BytesIO(output.getvalue().encode("utf-8")),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=trades.csv"},
        )
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


@app.get("/api/v1/export/stats.json")
async def export_stats_json():
    """Download unified stats snapshot as JSON."""
    try:
        import json as _json
        risk, vgx, mtbs, pmbs = await asyncio.gather(
            _cached_snapshot("risk", risk_snapshot),
            _cached_snapshot("vgx",  vgx_snapshot),
            _cached_snapshot("mtb",  mtb_snapshot),
            _cached_snapshot("pmb",  pmb_snapshot),
        )
        payload = {
            "unified":     await asyncio.to_thread(_unified_stats, vgx, mtbs, pmbs),
            "risk":        risk,
            "vgx":         vgx,
            "mtb":         mtbs,
            "pmb":         pmbs,
            "exported_at": datetime.now(timezone.utc).isoformat(),
        }
        content = _json.dumps(payload, indent=2, ensure_ascii=False)
        return StreamingResponse(
            io.BytesIO(content.encode("utf-8")),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=stats.json"},
        )
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


# ── VGX Grid Management Endpoints ─────────────────────────────────────────────


@app.get("/api/vgx/grid-config", response_class=JSONResponse)
async def vgx_get_grid_config():
    """Return current grid_config and grid_coins. Requires X-API-Key."""
    grid_cfg = await asyncio.to_thread(_vgx_get_grid_config)
    coins    = await asyncio.to_thread(_vgx_get_grid_coins)
    return JSONResponse(content={"grid_coins": coins, "grid_config": grid_cfg})


@app.get("/api/vgx/grid-coins", response_class=JSONResponse)
async def vgx_get_grid_coins():
    """Return the active VGX grid coin list. Requires X-API-Key."""
    coins = await asyncio.to_thread(_vgx_get_grid_coins)
    return JSONResponse(content={"coins": coins, "count": len(coins)})


@app.post("/api/vgx/base-price", response_class=JSONResponse)
async def vgx_set_coin_base_price(request: Request):
    """Set or update manual grid-centre base price for a coin. Requires X-API-Key."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(content={"status": "error", "reason": "invalid JSON body"})

    coin = str(body.get("coin", "")).strip().upper()
    if not coin or not coin.isalnum() or len(coin) > 10:
        return JSONResponse(content={
            "status": "error",
            "reason": "coin must be alphanumeric and at most 10 characters",
        })

    try:
        base_price = float(body.get("base_price", 0))
    except (TypeError, ValueError):
        return JSONResponse(content={"status": "error", "reason": "base_price must be a number"})

    if base_price <= 0:
        return JSONResponse(content={"status": "error", "reason": "base_price must be > 0"})

    ok = await asyncio.to_thread(_vgx_set_coin_base_price, coin, base_price, "dashboard")
    if not ok:
        return JSONResponse(content={"status": "error", "reason": "write failed"})

    logger.info("[VGX API] Base price set: coin=%s price=%s", coin, base_price)
    return JSONResponse(content={"status": "ok", "coin": coin, "base_price": base_price})


@app.delete("/api/vgx/base-price", response_class=JSONResponse)
async def vgx_remove_coin_base_price(request: Request):
    """Remove manual base price override for a coin. Coin supplied as query param. Requires X-API-Key."""
    coin = (request.query_params.get("coin", "")).strip().upper()
    if not coin or not coin.isalnum() or len(coin) > 10:
        return JSONResponse(content={
            "status": "error",
            "reason": "coin query param must be alphanumeric and at most 10 characters",
        })
    removed = await asyncio.to_thread(_vgx_remove_coin_base_price, coin)
    if not removed:
        logger.info("[VGX API] Base price remove: coin=%s not found", coin)
        return JSONResponse(content={"status": "not_found", "coin": coin})
    logger.info("[VGX API] Base price removed: coin=%s", coin)
    return JSONResponse(content={"status": "ok", "coin": coin})


@app.post("/api/vgx/grid-coins", response_class=JSONResponse)
async def vgx_set_grid_coins(request: Request):
    """Replace the active VGX grid coin list. Requires X-API-Key."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(content={"status": "error", "reason": "invalid JSON body"})

    coins = body.get("coins")
    if not isinstance(coins, list):
        return JSONResponse(content={"status": "error", "reason": "coins must be a list"})

    ok = await asyncio.to_thread(_vgx_set_grid_coins, coins)
    if not ok:
        return JSONResponse(content={
            "status": "error",
            "reason": "validation failed — list must be non-empty, each coin alphanumeric, max 20 coins",
        })

    # Return the persisted state (deduped, normalised) rather than the raw input.
    persisted = await asyncio.to_thread(_vgx_get_grid_coins)
    logger.info("[VGX API] Grid coins updated via API: %s", persisted)
    return JSONResponse(content={"status": "ok", "coins": persisted, "count": len(persisted)})


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "5000"))
    # proxy_headers=False: do not rewrite request.client from X-Forwarded-For.
    # This app is not behind a reverse proxy, so trusting that header would let
    # any client spoof its IP and bypass the failed-login throttle.
    uvicorn.run(app, host="0.0.0.0", port=port, proxy_headers=False)
