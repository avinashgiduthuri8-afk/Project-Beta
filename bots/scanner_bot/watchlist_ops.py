"""
watchlist_ops.py — Canonical watchlist mutation operations.

Single source of truth for add/remove logic.  Both app.py
(/api/watchlist/add, /api/watchlist/remove) and scanner_bot/main.py
(/api/v1/scanner/watchlist POST/DELETE) delegate here so pair-resolution
logic cannot diverge between callers.

Imports from bots.scanner_bot.main are done lazily (inside functions) to
avoid a circular import at module load time.
"""
from __future__ import annotations

import asyncio
import logging

import requests as _requests

from bots.scanner_bot.scanner import (
    WatchlistStore,
    resolve_coin_pair as _resolve_coin_pair,
    validate_coin_symbol,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _fetch_tickers() -> list:
    """Return the raw ticker list from the scanner's in-memory cache,
    falling back to a fresh CoinDCX API call when the cache is not yet warm.

    Returns [] on any network / API failure so callers treat an empty list
    as "cache not available" rather than a hard error.
    """
    import bots.scanner_bot.main as _sm  # lazy — avoids circular import
    sc = getattr(_sm, "_SCANNER", None)
    cached = getattr(sc, "_ticker_cache", None) if sc is not None else None
    if cached:
        return list(cached)
    try:
        resp = await asyncio.to_thread(
            _requests.get,
            "https://api.coindcx.com/exchange/ticker",
            timeout=6,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return []


def _wake_scanner() -> None:
    """Signal the scanner's refresh event so it picks up changes immediately."""
    try:
        import bots.scanner_bot.main as _sm  # lazy
        _sm._REFRESH_EVENT.set()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def add_coin(coin: str) -> dict:
    """Canonical add-coin operation.

    Steps:
      1. Format-validate the symbol via validate_coin_symbol().
      2. Resolve the best trading pair (INR > USDT) via resolve_coin_pair().
      3. Write to WatchlistStore and wake the scanner.

    Returns a result dict with keys:
      ok            bool   — True when coin is in the watchlist after the call
      already_existed bool — True when the coin was already present
      coin          str    — normalised symbol
      pair          str|None
      quote         str|None
      market        str|None  (backward-compat alias for quote)
      watchlist     list[str] — full watchlist after the operation
      reason        str|None  — "invalid_coin" | "no_pair_found" on failure
      error         str|None  — human-readable message on failure
    """
    import bots.scanner_bot.main as _sm  # lazy

    # 1. Format validation
    is_valid, symbol, _ = validate_coin_symbol(coin)
    if not is_valid:
        return {
            "ok": False,
            "already_existed": False,
            "coin": symbol,   # normalised form from validate_coin_symbol (may be "")
            "pair": None,
            "quote": None,
            "market": None,
            "watchlist": [],
            "reason": "invalid_coin",
            "error": "Invalid coin symbol",
        }

    # 2. Pair resolution
    tickers = await _fetch_tickers()
    resolved = _resolve_coin_pair(symbol, tickers=tickers if tickers else None)

    if not resolved["resolved"] and tickers:
        # Tickers are available but no INR/USDT pair found — reject.
        return {
            "ok": False,
            "already_existed": False,
            "coin": symbol,
            "pair": None,
            "quote": None,
            "market": None,
            "watchlist": [],
            "reason": "no_pair_found",
            "error": "Coin not available on CoinDCX (no INR or USDT pair found)",
        }

    pair  = resolved.get("pair")  or f"B-{symbol}_INR"
    quote = resolved.get("quote") or "INR"

    # 3. Store write
    sc = getattr(_sm, "_SCANNER", None)
    store: WatchlistStore = sc.watchlist_store if sc is not None else WatchlistStore()
    newly_added = store.add(symbol)          # True → new; False → already existed
    store.set_pair(symbol, pair, quote)      # always refresh pair metadata
    coins = store.all()

    _wake_scanner()

    return {
        "ok": True,
        "already_existed": not newly_added,
        "coin": symbol,
        "pair": pair,
        "quote": quote,
        "market": quote,   # backward-compat alias
        "watchlist": coins,
        "reason": None,
        "error": None,
    }


async def remove_coin(coin: str) -> dict:
    """Canonical remove-coin operation.  Idempotent — removing a coin that
    is not on the list succeeds silently.

    Returns a result dict with keys:
      ok        bool
      coin      str    — normalised symbol
      watchlist list[str]
      error     str|None
    """
    import bots.scanner_bot.main as _sm  # lazy

    symbol = coin.strip().upper()

    sc = getattr(_sm, "_SCANNER", None)
    store: WatchlistStore = sc.watchlist_store if sc is not None else WatchlistStore()
    store.remove(symbol)
    coins = store.all()

    _wake_scanner()

    return {
        "ok": True,
        "coin": symbol,
        "watchlist": coins,
        "error": None,
    }
