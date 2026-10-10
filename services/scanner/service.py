"""
PROJECT-ALPHA ScannerService.

Multi-timeframe scanner engine and signal generation pipeline.

Responsibilities:
  - Poll market candles natively from CoinDCX API / local cache
  - Multi-Timeframe (MTF) trend alignment (15m, 1h, 1d)
  - Pre-C2 5-stage filter cascade (Liquidity, Volume, Pump/Dump, Trend, Volatility)
  - C2 Confluence Evaluation (Chart 30%, Indicator 35%, Sentiment 20%, News 15%)
  - Deduplicate: publish SIGNAL_GENERATED for high-conviction signals
  - Detect expiry: publish SIGNAL_EXPIRED when live signals exceed TTL
  - Persist all signals to SignalRepository
"""

from __future__ import annotations

import asyncio
import inspect
import json
from datetime import datetime, timezone
from typing import Any

import httpx

from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.config import AppConfig
from core.logging import get_logger
from core.repository.candle_repo import CandleRepository
from core.repository.event_log_repo import EventLogRepository
from core.repository.position_repo import PositionRepository
from core.repository.signal_repo import SignalRepository
from core.repository.trade_repo import TradeRepository
from core.types import MarketState, Priority, Signal
from execution.trading.precision_rules import extract_base_coin
from shared.market.public_client import IndianStockPublicClient

from .adapter import raw_response_to_signals
from .calibration_worker import CalibrationWorker, get_data_file_path
from .confluence_engine import ConfluenceEngine
from .market_context import MarketContextService, calculate_ema
from .news_fetcher import NewsRiskService
from .signal_filter import (
    _dedup_key,
    deduplicate,
    detect_expired,
    filter_by_priority,
)

logger = get_logger("scanner.service")


class AsyncRateLimiter:
    """Token bucket rate limiter enforcing max N requests per second."""

    def __init__(self, max_rate: float = 8.0) -> None:
        self.max_rate = max_rate
        self._min_interval = 1.0 / max_rate
        self._last_call = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            elapsed = now - self._last_call
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            self._last_call = loop.time()


def canonical_to_coindcx_pair(pair: str) -> str:
    """Legacy name retained; normalize to a broker-style Indian stock symbol."""
    clean = pair.upper().strip().replace("/INR", "").replace(".NS", "")
    return clean if ":" in clean else f"NSE:{clean}"


class ScannerService:
    """
    Bridges V1 scanner → V2 event bus.

    Lifecycle:
        service = ScannerService(bus, signal_repo, event_log_repo, config)
        await service.start()         # subscribe handlers, called once at startup
        await service.poll()          # called by scheduler every N seconds
        await service.stop()          # unsubscribe, flush state
    """

    def __init__(
        self,
        bus: EventBus,
        signal_repo: SignalRepository,
        event_log_repo: EventLogRepository,
        config: AppConfig,
        candle_repo: CandleRepository | None = None,
        position_repo: PositionRepository | None = None,
        trade_repo: TradeRepository | None = None,
        market_context_service: MarketContextService | None = None,
        news_risk_service: NewsRiskService | None = None,
        calibration_worker: CalibrationWorker | None = None,
        learner_db_path: str | None = None,
    ) -> None:
        self._bus = bus
        self._signal_repo = signal_repo
        self._event_log = event_log_repo
        self._config = config
        self._candle_repo = candle_repo
        self._position_repo = position_repo
        self._trade_repo = trade_repo

        # Post-exit cooldown tracking {coin_symbol: {"exit_time": datetime, "exit_reason": str, "price": float}}
        self._cooldowns: dict[str, dict] = {}

        # Services for Macro Context, Sentiment, and News Risk
        self._market_context_service = market_context_service or MarketContextService()
        self._news_risk_service = news_risk_service or NewsRiskService(
            api_token=getattr(config, "cryptopanic_api_key", None)
        )
        self._rate_limiter = AsyncRateLimiter(max_rate=8.0)
        self._market_client = IndianStockPublicClient(rate_limit_per_sec=2.0)

        # In-memory live signal cache  {signal_id: Signal}
        self._live: dict[str, Signal] = {}
        # In-memory latest scan evaluated coins snapshot {symbol_or_pair: dict}
        self._latest_evaluated_coins: dict[str, dict] = {}
        self._market_snapshot: dict[str, dict[str, Any]] = {}
        self._last_funnel_counters: dict[str, int] = {}
        self._cumulative_funnel_counters: dict[str, int] = {
            "scanned": 0,
            "raw_universe": 0,
            "liquidity_rejected": 0,
            "spread_rejected": 0,
            "pump_dump_rejected": 0,
            "mtf_rejected": 0,
            "exhaustion_rejected": 0,
            "other_risk_rejected": 0,
            "qualified": 0,
            "signals_generated": 0,
        }
        # Dedup set — {coin::generated_at} for signals already seen this session
        self._seen_keys: set[str] = set()

        self._poll_count = 0
        self._last_poll_at: datetime | None = None
        self._last_error: str | None = None
        self._started = False
        self._poll_lock = asyncio.Lock()

        self._min_priority = Priority(self._config.scanner_min_priority)

        # C2 High-Conviction Confluence Engine (5-layer evaluation, strict rejection gate, max 1–2 signals)
        self._confluence_engine = ConfluenceEngine(
            strict_threshold=getattr(
                self._config, "scanner_strict_confluence_threshold", 85
            ),
            max_signals=getattr(self._config, "scanner_max_signals", 2),
        )

        # Dynamic Win-Rate Feedback Calibration Worker
        self._calibration_worker = calibration_worker or CalibrationWorker(
            bus=bus,
            confluence_engine=self._confluence_engine,
            feedback_db_path=learner_db_path,
        )

    @property
    def confluence_engine(self) -> ConfluenceEngine:
        return self._confluence_engine

    @property
    def market_context_service(self) -> MarketContextService:
        return self._market_context_service

    @property
    def news_risk_service(self) -> NewsRiskService:
        return self._news_risk_service

    @property
    def calibration_worker(self) -> CalibrationWorker:
        return self._calibration_worker

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Subscribe bus handlers. Called once at application startup."""
        if self._started:
            return
        self._started = True
        self._bus.subscribe(EventType.POSITION_CLOSED, self.on_position_closed)
        self._bus.subscribe(EventType.TRADE_EXECUTED, self.on_trade_executed)
        await self._bus.publish(
            EventType.SYSTEM_STARTUP,
            {"service": "scanner_service"},
        )
        logger.info("ScannerService started")

        # Bootstrap active post-exit cooldowns from recent closed trades
        if self._trade_repo:
            try:
                recent_trades = await self._trade_repo.get_recent(limit=30)
                now = datetime.now(timezone.utc)
                cooldown_dur = getattr(self._config, "post_exit_cooldown_seconds", 900)
                for tr in recent_trades:
                    c_time = getattr(tr, "exit_time", None) or getattr(
                        tr, "closed_at", None
                    )
                    if c_time:
                        if c_time.tzinfo is None:
                            c_time = c_time.replace(tzinfo=timezone.utc)
                        if (now - c_time).total_seconds() < cooldown_dur:
                            coin_clean = (
                                (getattr(tr, "coin", "") or "")
                                .upper()
                                .replace("/INR", "")
                                .replace("/USDT", "")
                                .replace("B-", "")
                            )
                            if coin_clean and coin_clean not in self._cooldowns:
                                self._cooldowns[coin_clean] = {
                                    "exit_time": c_time,
                                    "exit_reason": getattr(tr, "exit_reason", "CLOSED"),
                                    "price": float(getattr(tr, "price", 0.0) or 0.0),
                                }
                if self._cooldowns:
                    logger.info(
                        "Bootstrapped %d active post-exit cooldowns: %s",
                        len(self._cooldowns),
                        list(self._cooldowns.keys()),
                    )
            except Exception as exc:
                logger.debug("Could not bootstrap post-exit cooldowns: %s", exc)

        # Start dynamic calibration worker
        await self._calibration_worker.start()

        # Database-first candle bootstrapping and periodic flushing
        if self._candle_repo:
            asyncio.create_task(self.bootstrap_candles())
            self._flusher_task = asyncio.create_task(self._candle_flusher_loop())

    async def stop(self) -> None:
        """Unsubscribe and flush in-memory state."""
        self._started = False
        self._bus.unsubscribe(EventType.POSITION_CLOSED, self.on_position_closed)
        self._bus.unsubscribe(EventType.TRADE_EXECUTED, self.on_trade_executed)
        self._live.clear()

        # Stop calibration worker
        await self._calibration_worker.stop()
        await self._market_client.close()

        # Stop background flusher loop
        if hasattr(self, "_flusher_task") and self._flusher_task:
            self._flusher_task.cancel()
            try:
                await self._flusher_task
            except asyncio.CancelledError:
                pass
            self._flusher_task = None

        logger.info("ScannerService stopped")

    # ── Signal Lifecycle & Cooldown Handlers ──────────────────────────────────

    async def on_position_closed(self, event_type: EventType, payload: dict) -> None:
        """Record post-exit cooldown and invalidate any live signals for this coin."""
        coin = (
            (payload.get("coin") or "")
            .upper()
            .replace("/INR", "")
            .replace("/USDT", "")
            .replace("B-", "")
        )
        if not coin:
            return
        now = datetime.now(timezone.utc)
        exit_reason = payload.get("exit_reason", "CLOSED")
        exit_price = float(payload.get("exit_price") or payload.get("price") or 0.0)
        cooldown_dur = getattr(self._config, "post_exit_cooldown_seconds", 900)
        self._cooldowns[coin] = {
            "exit_time": now,
            "exit_reason": exit_reason,
            "price": exit_price,
        }
        logger.info(
            "Post-exit cooldown started for %s (%s, duration: %ds)",
            coin,
            exit_reason,
            cooldown_dur,
        )

        # Evict any active in-memory live signals for this coin
        to_evict = [
            sid
            for sid, sig in self._live.items()
            if sig.coin.upper()
            .replace("/INR", "")
            .replace("/USDT", "")
            .replace("B-", "")
            == coin
        ]
        for sid in to_evict:
            sig = self._live.pop(sid, None)
            if sig:
                try:
                    await self._signal_repo.mark_expired(sid)
                except Exception:
                    pass
        # Clear seen keys for this coin so it can be re-scanned once cooldown expires
        keys_to_discard = [k for k in self._seen_keys if k.startswith(f"{coin}::")]
        for k in keys_to_discard:
            self._seen_keys.discard(k)

    async def on_trade_executed(self, event_type: EventType, payload: dict) -> None:
        """Mark signal consumed when trade executes."""
        signal_id = payload.get("signal_id")
        coin = (
            (payload.get("coin") or "")
            .upper()
            .replace("/INR", "")
            .replace("/USDT", "")
            .replace("B-", "")
        )
        if signal_id:
            self._live.pop(signal_id, None)
            try:
                await self._signal_repo.mark_consumed(signal_id)
            except Exception:
                pass
        # Also clean up any other live signals for this coin
        if coin:
            to_evict = [
                sid
                for sid, sig in self._live.items()
                if sig.coin.upper()
                .replace("/INR", "")
                .replace("/USDT", "")
                .replace("B-", "")
                == coin
            ]
            for sid in to_evict:
                self._live.pop(sid, None)
            # Evict from seen keys so coin can be evaluated again after trade close & cooldown
            keys_to_discard = [k for k in self._seen_keys if k.startswith(f"{coin}::")]
            for k in keys_to_discard:
                self._seen_keys.discard(k)

    def is_in_cooldown(self, coin: str) -> bool:
        """Check if a coin is currently in post-exit cooldown window."""
        coin_clean = (
            coin.upper()
            .strip()
            .replace("/INR", "")
            .replace("/USDT", "")
            .replace("B-", "")
        )
        info = self._cooldowns.get(coin_clean)
        if not info or "exit_time" not in info:
            return False
        exit_time = info["exit_time"]
        cooldown_dur = float(getattr(self._config, "post_exit_cooldown_seconds", 900))
        elapsed = (datetime.now(timezone.utc) - exit_time).total_seconds()
        return elapsed < cooldown_dur

    # ── Database-First Bootstrapping & Periodic Candle Cache Flushing ────────
    async def bootstrap_candles(self) -> None:
        """Database-first bootstrapping: warm up market_candles table for all watchlist coins."""
        logger.info("[Bootstrap] Starting database-first candle warm-up...")
        try:
            coins = await self._fetch_watchlist_coins()
            pairs = [canonical_to_coindcx_pair(coin) for coin in coins]

            for pair in pairs:
                for timeframe in ["15m", "1h", "1d"]:
                    try:
                        db_candles = await self._candle_repo.get_recent_candles(
                            pair, timeframe, limit=120
                        )
                        if len(db_candles) < 120:
                            logger.info(
                                "[Bootstrap] Insufficient cached candles for pair=%s timeframe=%s (%d/120). Catching up from exchange API...",
                                pair,
                                timeframe,
                                len(db_candles),
                            )
                            coindcx_pair = canonical_to_coindcx_pair(pair)
                            interval = timeframe
                            raw_candles = await self._fetch_coindcx_candles(
                                coindcx_pair, interval, limit=120
                            )

                            if raw_candles:
                                formatted = []
                                for c in raw_candles:
                                    try:
                                        ts_ms = int(c.get("time", c.get("t", 0)) or 0)
                                        close = float(
                                            c.get("close", c.get("c", 0.0)) or 0.0
                                        )
                                        if ts_ms <= 0 or close <= 0:
                                            continue
                                        formatted.append(
                                            {
                                                "pair": pair,
                                                "timeframe": timeframe,
                                                "timestamp": ts_ms,
                                                "open": float(
                                                    c.get("open", c.get("o", close))
                                                ),
                                                "high": float(
                                                    c.get("high", c.get("h", close))
                                                ),
                                                "low": float(
                                                    c.get("low", c.get("l", close))
                                                ),
                                                "close": close,
                                                "volume": float(
                                                    c.get("volume", c.get("v", 0.0))
                                                ),
                                            }
                                        )
                                    except (TypeError, ValueError, KeyError):
                                        continue

                                if formatted:
                                    await self._candle_repo.upsert_candles(formatted)
                                    logger.info(
                                        "[Bootstrap] Idempotent upserted %d candles for pair=%s timeframe=%s",
                                        len(formatted),
                                        pair,
                                        timeframe,
                                    )
                        else:
                            logger.info(
                                "[Bootstrap] Sufficient cached candles found in SQLite for pair=%s timeframe=%s (%d)",
                                pair,
                                timeframe,
                                len(db_candles),
                            )
                    except Exception as exc:
                        logger.warning(
                            "[Bootstrap] Failed to bootstrap candles for pair=%s timeframe=%s: %s",
                            pair,
                            timeframe,
                            exc,
                        )
            logger.info("[Bootstrap] Database-first candle warm-up complete.")
        except Exception as exc:
            logger.exception(
                "[Bootstrap] Critical failure during candle warm-up",
                extra={"error": str(exc)},
            )

    async def _fetch_watchlist_coins(self) -> list[str]:
        """Return a market-wide, deterministic Top-50 pair universe.

        The old implementation made the local watchlist the scanner universe.
        A watchlist is useful for display, but it must not prevent discovery of
        liquid markets.  Tickers are fetched once, cheap liquidity/spread
        filters run before candles, and the composite rank is stable on ties.
        """
        try:
            tickers = await self._market_client.get_tickers()
            markets = []
            for item in tickers:
                last = float(item.get("last_price") or 0.0)
                volume = float(item.get("volume") or 0.0)
                if last <= 0.0 or volume <= 0.0:
                    continue
                market = str(item.get("market") or "").upper()
                turnover = float(item.get("turnover") or volume * last)
                markets.append(
                    {
                        "pair": market,
                        "turnover": turnover,
                        "spread_pct": 0.0,
                        "change_24h": abs(float(item.get("change_24_hour") or 0.0)),
                        "last_price": last,
                    }
                )
            markets.sort(key=lambda item: (-item["turnover"], item["pair"]))
            top_n = int(getattr(self._config, "scanner_ranking_top_n", 50) or 50)
            selected = markets[:top_n]
            self._market_snapshot = {item["pair"]: item for item in selected}
            self._last_funnel_counters.update(
                {
                    "market_universe": len(markets),
                    "market_liquidity_rejected": 0,
                    "market_top_n": len(selected),
                }
            )
            return [item["pair"] for item in selected]
        except Exception as exc:
            logger.warning("Indian public-market discovery failed: %s", exc)
            self._market_snapshot = {}
            return []

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await asyncio.sleep(0.125)
                response = await client.get("https://api.coindcx.com/exchange/ticker")
                response.raise_for_status()
                raw = response.json()
        except Exception as exc:
            logger.warning("Market-wide ticker discovery failed: %s", exc)
            raw = []

        if isinstance(raw, list):
            markets: list[dict[str, Any]] = []
            for item in raw:
                if not isinstance(item, dict):
                    continue
                market = str(item.get("market") or "").upper()
                if market.endswith("INR"):
                    base, quote = market[:-3], "INR"
                elif market.endswith("USDT"):
                    base, quote = market[:-4], "USDT"
                else:
                    continue
                if not base:
                    continue
                try:
                    last = float(item.get("last_price") or 0.0)
                    bid = float(item.get("bid") or 0.0)
                    ask = float(item.get("ask") or 0.0)
                    volume = float(item.get("volume") or 0.0)
                    change = abs(float(item.get("change_24_hour") or 0.0))
                except (TypeError, ValueError):
                    continue
                if last <= 0.0 or volume <= 0.0:
                    continue
                turnover = volume * last
                mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else last
                spread_pct = abs(ask - bid) / mid * 100.0 if mid > 0 else 100.0
                markets.append(
                    {
                        "pair": f"{base}/{quote}",
                        "turnover": turnover,
                        "spread_pct": spread_pct,
                        "change_24h": change,
                        "last_price": last,
                    }
                )

            self._market_snapshot = {m["pair"]: m for m in markets}
            min_turnover = float(
                getattr(self._config, "scanner_min_24h_volume", 200000.0) or 200000.0
            )
            max_spread = float(
                getattr(self._config, "scanner_max_spread_pct", 1.0) or 1.0
            )
            eligible = [
                m
                for m in markets
                if m["turnover"] >= min_turnover and m["spread_pct"] <= max_spread
            ]
            # Log-normal-ish bounded score: turnover is the primary ranking
            # signal, with a spread penalty and a modest volatility tie-breaker.
            max_turnover = max((m["turnover"] for m in eligible), default=1.0)
            for m in eligible:
                m["market_rank_score"] = round(
                    0.65 * min(100.0, 100.0 * m["turnover"] / max_turnover)
                    + 0.25 * max(0.0, 100.0 - m["spread_pct"] * 20.0)
                    + 0.10 * min(100.0, m["change_24h"] * 4.0),
                    6,
                )
            eligible.sort(key=lambda m: (-m["market_rank_score"], m["pair"]))
            top_n = int(getattr(self._config, "scanner_ranking_top_n", 50) or 50)
            selected = eligible[:top_n]
            self._market_snapshot = {m["pair"]: m for m in selected}
            self._last_funnel_counters.update(
                {
                    "market_universe": len(markets),
                    "market_liquidity_rejected": len(markets) - len(eligible),
                    "market_top_n": len(selected),
                }
            )
            return [m["pair"] for m in selected]

        # A temporary public-market outage must not silently fall back to a
        # narrow watchlist; return no candidates and expose the failure.
        self._market_snapshot = {}
        self._last_funnel_counters.update(
            {
                "market_universe": 0,
                "market_liquidity_rejected": 0,
                "market_top_n": 0,
            }
        )
        return []

    async def _get_candles_for_pair(
        self,
        pair: str,
        interval: str,
        limit: int = 120,
        max_timestamp_ms: int | None = None,
    ) -> list[dict]:
        """Read candles from the repository first, then public CoinDCX data."""
        candles: list[dict] = []
        if self._candle_repo:
            try:
                candles = await self._candle_repo.get_recent_candles(
                    pair, interval, limit=limit, max_timestamp_ms=max_timestamp_ms
                )
            except Exception:
                candles = []
        if candles:
            return candles
        return await self._fetch_coindcx_candles(
            canonical_to_coindcx_pair(pair), interval, limit=limit
        )

    async def _fetch_watchlist_coins_legacy(self) -> list[str]:
        """Read the local watchlist for UI/backward-compatible callers."""
        data_path = get_data_file_path("watchlist.json")
        try:
            if data_path.exists():
                with open(data_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and "coins" in data:
                        return [str(c).upper() for c in data["coins"]]
                    if isinstance(data, list):
                        return [str(c).upper() for c in data]
        except Exception as exc:
            logger.debug("Could not read watchlist.json, using defaults: %s", exc)

        # Default canonical CoinDCX watchlist
        return [
            "BTC",
            "ETH",
            "SOL",
            "BNB",
            "XRP",
            "ZEC",
            "AVAX",
            "LINK",
            "DOGE",
            "SHIB",
            "MATIC",
        ]

    async def _fetch_coindcx_candles(
        self, coindcx_pair: str, interval: str, limit: int = 120
    ) -> list[dict]:
        """Fetch keyless NSE/BSE candles through the public data adapter."""
        try:
            return await self._market_client.get_candles(
                coindcx_pair, interval=interval, limit=limit
            )
        except Exception as exc:
            logger.debug(
                "Failed to fetch public Indian-equity candles for pair=%s: %s",
                coindcx_pair,
                exc,
            )
        return []

    async def _candle_flusher_loop(self) -> None:
        """Background task to fetch latest closed candles from CoinDCX and flush to database."""
        logger.info("[Flusher] Background candle flusher loop started")
        while self._started:
            try:
                # Wait for the configured scanner poll interval
                await asyncio.sleep(self._config.scanner_poll_interval)
                if not self._started:
                    break

                logger.info("[Flusher] Periodic candle flush cycle started")
                coins = await self._fetch_watchlist_coins()
                pairs = [canonical_to_coindcx_pair(coin) for coin in coins]

                for pair in pairs:
                    for timeframe in ["15m", "1h", "1d"]:
                        coindcx_pair = canonical_to_coindcx_pair(pair)
                        interval = timeframe
                        # Limit to last 5 candles to catch the latest closed ones
                        raw_candles = await self._fetch_coindcx_candles(
                            coindcx_pair, interval, limit=5
                        )

                        if raw_candles:
                            formatted = []
                            for c in raw_candles:
                                try:
                                    ts_ms = int(c.get("time", c.get("t", 0)) or 0)
                                    close = float(
                                        c.get("close", c.get("c", 0.0)) or 0.0
                                    )
                                    if ts_ms <= 0 or close <= 0:
                                        continue
                                    formatted.append(
                                        {
                                            "pair": pair,
                                            "timeframe": timeframe,
                                            "timestamp": ts_ms,
                                            "open": float(
                                                c.get("open", c.get("o", close))
                                            ),
                                            "high": float(
                                                c.get("high", c.get("h", close))
                                            ),
                                            "low": float(
                                                c.get("low", c.get("l", close))
                                            ),
                                            "close": close,
                                            "volume": float(
                                                c.get("volume", c.get("v", 0.0))
                                            ),
                                        }
                                    )
                                except (TypeError, ValueError, KeyError):
                                    continue

                            if formatted:
                                await self._candle_repo.upsert_candles(formatted)

                logger.info(
                    "[Flusher] Periodic candle flush cycle completed successfully"
                )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception(
                    "[Flusher] Unexpected error in candle flusher loop",
                    extra={"error": str(exc)},
                )

    # ── True Multi-Timeframe (MTF) Data Fetching ─────────────────────────────
    async def fetch_mtf_candles(self, coindcx_pair: str) -> dict[str, list[dict]]:
        """
        Fetch true discrete 5m, 15m, and 1h candle feeds with 8 req/s rate-limiting gate.
        """
        timeframes = ["15m", "1h", "1d"]
        results: dict[str, list[dict]] = {}
        for tf in timeframes:
            raw = await self._fetch_coindcx_candles(coindcx_pair, interval=tf, limit=30)
            results[tf] = raw
        return results

    def evaluate_mtf_alignment(
        self,
        mtf_candles: dict[str, list[dict]],
    ) -> tuple[bool, dict]:
        """
        Calculates Fast EMA(9) vs Slow EMA(21) and momentum individually per timeframe.
        Returns: (is_aligned, indicator_details)
        """
        details: dict = {}
        tf_aligned = {}

        for tf, candles in mtf_candles.items():
            closes = []
            for c in candles:
                val = c.get("close", c.get("c", c.get("price", 0.0)))
                try:
                    closes.append(float(val))
                except (ValueError, TypeError):
                    continue

            if len(closes) >= 21:
                ema9 = calculate_ema(closes, 9)[-1]
                ema21 = calculate_ema(closes, 21)[-1]
                momentum = closes[-1] - closes[-2] if len(closes) >= 2 else 0.0
                aligned = ema9 >= ema21
                details[tf] = {
                    "ema9": round(ema9, 4),
                    "ema21": round(ema21, 4),
                    "momentum": round(momentum, 4),
                    "aligned": aligned,
                }
                tf_aligned[tf] = aligned
            else:
                details[tf] = {"aligned": True, "note": "insufficient_bars"}
                tf_aligned[tf] = True

        # MTF alignment: prefer 4h and 1d if provided; otherwise check available higher timeframes
        if "1h" in tf_aligned or "1d" in tf_aligned:
            is_aligned = tf_aligned.get("1h", True) and tf_aligned.get("1d", True)
        elif "15m" in tf_aligned:
            is_aligned = tf_aligned.get("15m", True) and tf_aligned.get("15m", True)
        else:
            is_aligned = all(tf_aligned.values()) if tf_aligned else True
        return is_aligned, details

    # ── Polling (called by scheduler) ─────────────────────────────────────────

    async def poll(self, max_timestamp_ms: int | None = None) -> dict:
        """
        Fetch fresh signals from V1 scanner, update live cache, and publish events.

        Returns a summary dict for scheduler logging.
        """
        summary = {
            "fetched": 0,
            "new_signals": 0,
            "expired": 0,
            "errors": 0,
            "next_interval_s": self._config.scanner_poll_interval,
        }

        if self._poll_lock.locked():
            logger.warning("Scanner poll skipped: previous poll is still running")
            return summary

        await self._poll_lock.acquire()
        try:
            # 1. Refresh Live Macro Market Context (BTC, ETH, Fear & Greed)
            market_context = await self._market_context_service.refresh_market_context()
            summary["next_interval_s"] = self.get_adaptive_poll_interval(market_context)
            self._confluence_engine.update_market_sentiment(
                btc_trend=market_context.get("btc_trend", "BULLISH"),
                eth_trend=market_context.get("eth_trend", "BULLISH"),
                regime=market_context.get("market_regime", "RISK_ON"),
                fear_greed=market_context.get("fear_and_greed", 50),
            )

            # 2. Refresh News Feeds
            await self._news_risk_service.fetch_latest_news()

            # 3. Fetch candidate signals (B1 composite ranking & B2 5-stage cascade)
            raw = await self._fetch_v1_signals(max_timestamp_ms=max_timestamp_ms)
            summary["fetched"] = len(raw)

            # B7: Early Lock Suppression — filter out coins with active positions or cooldowns BEFORE C2/AI compute
            open_coins: set[str] = set()
            if self._position_repo:
                try:
                    open_positions = await self._position_repo.get_open()
                    for p in open_positions:
                        p_clean = extract_base_coin(
                            getattr(p, "coin", "")
                        ) or extract_base_coin(getattr(p, "pair", ""))
                        if p_clean:
                            open_coins.add(p_clean.upper())
                except Exception as pos_err:
                    logger.warning(
                        "Could not check open positions for lock suppression: %s",
                        pos_err,
                    )

            # Clean up expired cooldowns
            now_utc = datetime.now(timezone.utc)
            cooldown_dur = getattr(self._config, "post_exit_cooldown_seconds", 900)
            expired_cooldowns = []
            for c_coin, c_info in list(self._cooldowns.items()):
                c_exit = c_info["exit_time"]
                if c_exit.tzinfo is None:
                    c_exit = c_exit.replace(tzinfo=timezone.utc)
                elapsed = (now_utc - c_exit).total_seconds()
                if elapsed >= cooldown_dur:
                    expired_cooldowns.append(c_coin)
            for c_coin in expired_cooldowns:
                del self._cooldowns[c_coin]
                logger.info(
                    "Post-exit cooldown expired for %s. Re-entry allowed.", c_coin
                )

            actionable_raw = []
            live_sigs = self.get_live_signals()
            live_coins = set()
            for s in live_sigs:
                if max_timestamp_ms:
                    s_exp = s.expires_at.timestamp() * 1000
                    if max_timestamp_ms < s_exp:
                        live_coins.add(s.coin.upper())
                elif s.is_live:
                    live_coins.add(s.coin.upper())
            for c in raw:
                c_coin = (c.get("coin") or "").upper()
                if not c_coin:
                    continue
                if c_coin in open_coins:
                    logger.debug(
                        "Early lock suppression: %s has active open position. Skipping C2/AI compute.",
                        c_coin,
                    )
                    continue
                if self.is_in_cooldown(c_coin):
                    logger.debug(
                        "Early lock suppression: %s is in post-exit cooldown. Skipping C2/AI compute.",
                        c_coin,
                    )
                    continue
                if c_coin in live_coins:
                    logger.debug(
                        "Early lock suppression: %s has live unexpired signal active. Skipping C2/AI.",
                        c_coin,
                    )
                    continue
                actionable_raw.append(c)

            # 4. Adapt candidate signals to domain (Timeframe-aware TTL B8 & Breakout TTL Phase 3A)
            candidates = raw_response_to_signals(
                actionable_raw,
                signal_ttl_seconds=getattr(self._config, "scanner_signal_ttl", 300),
                breakout_ttl_seconds=getattr(self._config, "scanner_breakout_ttl", 300),
            )

            # 5. Enrich raw candidate dicts with News Risk Evaluations
            enriched_raw = []
            cand_by_coin: dict[str, dict] = {}
            for c in actionable_raw:
                c_pair = (c.get("pair") or "").upper()
                c_coin = (c.get("coin") or "").upper()
                if c_pair:
                    cand_by_coin[c_pair] = c
                if c_coin:
                    cand_by_coin.setdefault(c_coin, c)

            for sig in candidates:
                cand_dict = cand_by_coin.get(sig.pair.upper()) or cand_by_coin.get(
                    sig.coin.upper(), {"coin": sig.coin}
                )
                cand_dict["news"] = self._news_risk_service.evaluate_coin_news(sig.coin)
                enriched_raw.append(cand_dict)

            # 6. Filter by minimum priority
            candidates = filter_by_priority(candidates, self._min_priority)

            # 7. Evaluate through C2 5-Layer Confluence Engine & Strict Rejection Gate (B3, B4, B5)
            # B3: Macro Regime (RISK_ON / RISK_OFF / BTC trend) applies bounded +/-5 score adjustment
            # B4: Dynamic Threshold (80-92) responds solely to volatility & sideways chop
            btc_trend = market_context.get("btc_trend", "SIDEWAYS")
            market_regime = market_context.get("market_regime", "RISK_ON")
            is_choppy = btc_trend == "SIDEWAYS" and market_regime == "RISK_OFF"
            high_conviction_signals, eval_results = (
                self._confluence_engine.evaluate_candidates(
                    raw_candidates=enriched_raw,
                    signals=candidates,
                    market_volatility=1.0,
                    is_choppy=is_choppy,
                )
            )

            # Preserve the C2 decision and MTF evidence on the domain signal.
            # AI, risk, and Telegram consumers all receive this
            # same immutable decision metadata rather than recomputing it.
            for res in eval_results:
                payload = res.signal.raw_payload or {}
                payload.update(
                    {
                        "confluence_score": res.confluence_score,
                        "confluence_base_score": res.base_score,
                        "regime_adjustment": res.regime_adjustment,
                        "dynamic_threshold": res.dynamic_threshold,
                        "confluence_accepted": res.accepted,
                        "confluence_rejection_reasons": list(res.rejection_reasons),
                        "mtf_timeframes": ["15m", "1h", "1d"],
                    }
                )
                res.signal.raw_payload = payload

                if not res.accepted:
                    reasons_str = " ".join(res.rejection_reasons)
                    if "RSI_EXHAUSTION" in reasons_str or "DECLINING_VOLUME" in reasons_str:
                        self._last_funnel_counters["exhaustion_rejected"] = (
                            self._last_funnel_counters.get("exhaustion_rejected", 0) + 1
                        )
                    elif "MTF_MISMATCH" in reasons_str or "Missing multi-timeframe" in reasons_str:
                        self._last_funnel_counters["mtf_rejected"] = (
                            self._last_funnel_counters.get("mtf_rejected", 0) + 1
                        )
                    else:
                        self._last_funnel_counters["other_risk_rejected"] = (
                            self._last_funnel_counters.get("other_risk_rejected", 0) + 1
                        )

            self._last_funnel_counters["qualified"] = len(high_conviction_signals)

            # 8. Retain latest-scan evaluation snapshot in memory (atomic replacement)
            new_eval_snapshot: dict[str, dict] = {}
            for res in eval_results:
                coin_sym = res.signal.coin.upper()
                cand_raw = cand_by_coin.get(coin_sym, {})

                # Determine EMA trend
                if coin_sym == "BTC":
                    ema_trend = (
                        self._market_context_service.get_current_sentiment().get(
                            "btc_trend", "SIDEWAYS"
                        )
                    )
                elif coin_sym == "ETH":
                    ema_trend = (
                        self._market_context_service.get_current_sentiment().get(
                            "eth_trend", "SIDEWAYS"
                        )
                    )
                else:
                    ema_trend = "BULLISH" if res.signal.mtf_alignment else "SIDEWAYS"

                raw_payload = res.signal.raw_payload or {}
                price_val = float(
                    cand_raw.get("price")
                    or raw_payload.get("price")
                    or raw_payload.get("close")
                    or 0.0
                )
                vol_24h = float(
                    cand_raw.get("volume_24h")
                    or cand_raw.get("volume")
                    or raw_payload.get("volume_24h")
                    or 0.0
                )
                vol_ratio = float(
                    cand_raw.get("volume_spike_ratio")
                    or cand_raw.get("volume_ratio")
                    or raw_payload.get("volume_spike_ratio")
                    or 1.0
                )
                rsi_val = float(
                    cand_raw.get("rsi")
                    or raw_payload.get("rsi_14")
                    or raw_payload.get("rsi")
                    or 50.0
                )

                eval_item = {
                    "symbol": coin_sym,
                    "coin": coin_sym,
                    "pair": res.signal.pair,
                    "price": price_val,
                    "volume_24h": vol_24h,
                    "volume_ratio": vol_ratio,
                    "ema_trend": ema_trend,
                    "rsi": rsi_val,
                    "mtf_alignment": "15m_1h_1d"
                    if res.signal.mtf_alignment
                    else "none",
                    "is_mtf_aligned": bool(res.signal.mtf_alignment),
                    "confluence_score": res.confluence_score,
                    "status": "PASSED" if res.accepted else "REJECTED",
                    "accepted": res.accepted,
                    "eval_breakdown": {
                        "chart": (
                            {
                                "score": res.layer_evaluations["chart"].score,
                                "passed": res.layer_evaluations["chart"].passed,
                                "details": res.layer_evaluations["chart"].details,
                                "reasons": res.layer_evaluations["chart"].reasons,
                            }
                            if "chart" in res.layer_evaluations
                            else {}
                        ),
                        "indicator": (
                            {
                                "score": res.layer_evaluations["indicator"].score,
                                "passed": res.layer_evaluations["indicator"].passed,
                                "details": res.layer_evaluations["indicator"].details,
                                "reasons": res.layer_evaluations["indicator"].reasons,
                            }
                            if "indicator" in res.layer_evaluations
                            else {}
                        ),
                        "sentiment": (
                            {
                                "score": res.layer_evaluations["sentiment"].score,
                                "passed": res.layer_evaluations["sentiment"].passed,
                                "details": res.layer_evaluations["sentiment"].details,
                                "reasons": res.layer_evaluations["sentiment"].reasons,
                            }
                            if "sentiment" in res.layer_evaluations
                            else {}
                        ),
                        "news": (
                            {
                                "score": res.layer_evaluations["news"].score,
                                "passed": res.layer_evaluations["news"].passed,
                                "details": res.layer_evaluations["news"].details,
                                "reasons": res.layer_evaluations["news"].reasons,
                            }
                            if "news" in res.layer_evaluations
                            else {}
                        ),
                    },
                    "rejection_reasons": res.rejection_reasons,
                    "rejection_reason": (
                        "; ".join(res.rejection_reasons)
                        if res.rejection_reasons
                        else None
                    ),
                    "evaluated_at": datetime.now(timezone.utc).isoformat(),
                }
                new_eval_snapshot[coin_sym] = eval_item
                new_eval_snapshot[res.signal.pair.upper()] = eval_item
                clean_pair = (
                    res.signal.pair.replace("/", "")
                    .replace("_", "")
                    .replace("-", "")
                    .upper()
                )
                new_eval_snapshot[clean_pair] = eval_item

            self._latest_evaluated_coins = new_eval_snapshot

            # 9. Cooldown & open positions defense-in-depth, then deduplicate against seen set
            valid_signals = [
                s
                for s in high_conviction_signals
                if s.coin.upper() not in self._cooldowns
                and s.coin.upper() not in open_coins
            ]
            new_signals, new_keys = deduplicate(valid_signals, self._seen_keys)
            self._seen_keys.update(new_keys)

            # 11. Persist new signals and publish events
            for sig in new_signals:
                await self._signal_repo.insert(sig)
                self._live[sig.id] = sig
                await self._publish_signal_generated(sig)
                summary["new_signals"] += 1

            self._last_funnel_counters["signals_generated"] = (
                self._last_funnel_counters.get("signals_generated", 0) + len(new_signals)
            )
            for k in (
                "scanned",
                "raw_universe",
                "liquidity_rejected",
                "spread_rejected",
                "pump_dump_rejected",
                "mtf_rejected",
                "exhaustion_rejected",
                "other_risk_rejected",
                "qualified",
                "signals_generated",
            ):
                if k in self._last_funnel_counters:
                    self._cumulative_funnel_counters[k] = (
                        self._cumulative_funnel_counters.get(k, 0)
                        + self._last_funnel_counters[k]
                    )

            # 12. Detect expiry in live cache
            live_list = list(self._live.values())
            now_utc = (
                datetime.fromtimestamp(max_timestamp_ms / 1000.0, tz=timezone.utc)
                if max_timestamp_ms
                else datetime.now(timezone.utc)
            )
            still_live, newly_expired = detect_expired(live_list, current_time=now_utc)

            for sig in newly_expired:
                del self._live[sig.id]
                self._seen_keys.discard(_dedup_key(sig))
                await self._signal_repo.mark_expired(sig.id, reason="TTL")
                await self._publish_signal_expired(sig)
                summary["expired"] += 1

            self._poll_count += 1
            self._last_poll_at = datetime.now(timezone.utc)
            self._last_error = None
            logger.info(
                "Scanner poll complete",
                extra={
                    **summary,
                    "live_count": len(self._live),
                    "evaluated_count": len(eval_results),
                },
            )

        except Exception as exc:
            self._last_error = str(exc)
            summary["errors"] = 1
            logger.exception("Scanner poll failed", extra={"error": str(exc)})
        finally:
            self._poll_lock.release()

        return summary

    async def check_expiry(self) -> int:
        """
        Check in-memory live signals for expiry (called by signal_expiry_check job).
        Returns count of signals expired.
        """
        live_list = list(self._live.values())
        _, newly_expired = detect_expired(live_list)
        for sig in newly_expired:
            del self._live[sig.id]
            self._seen_keys.discard(_dedup_key(sig))
            await self._signal_repo.mark_expired(sig.id, reason="TTL")
            await self._publish_signal_expired(sig)
        return len(newly_expired)

    # ── Public query interface ─────────────────────────────────────────────────

    def get_live_signals(self) -> list[Signal]:
        """Return current live signals sorted by score desc."""
        return sorted(self._live.values(), key=lambda s: s.score, reverse=True)

    def get_scanned_coins(
        self,
        min_score: int | None = None,
        limit: int = 50,
        sort_by: str = "confluence_score",
    ) -> list[dict]:
        """Return unique evaluated coins from the latest scan pass."""
        unique_coins: dict[str, dict] = {}
        for k, item in self._latest_evaluated_coins.items():
            pair = item.get("pair") or item.get("symbol")
            if pair not in unique_coins:
                unique_coins[pair] = item

        items = list(unique_coins.values())
        if min_score is not None:
            items = [c for c in items if c.get("confluence_score", 0) >= min_score]

        if sort_by == "confluence_score":
            items.sort(key=lambda c: c.get("confluence_score", 0), reverse=True)
        elif sort_by == "price":
            items.sort(key=lambda c: c.get("price", 0.0), reverse=True)
        elif sort_by == "symbol":
            items.sort(key=lambda c: c.get("symbol", ""))

        return items[:limit]

    def get_scanned_coin_detail(self, symbol: str) -> dict | None:
        """Return detail for a specific scanned coin (case-insensitive, handles BTC, BTCINR, BTC/INR)."""
        if not symbol:
            return None
        sym_clean = symbol.strip().upper()
        # Direct lookup
        if sym_clean in self._latest_evaluated_coins:
            return self._latest_evaluated_coins[sym_clean]

        # Clean alphanumeric lookup
        sym_alpha = sym_clean.replace("/", "").replace("_", "").replace("-", "")
        if sym_alpha in self._latest_evaluated_coins:
            return self._latest_evaluated_coins[sym_alpha]

        # Suffix / symbol matching
        for k, v in self._latest_evaluated_coins.items():
            if k.upper() == sym_clean or v.get("symbol", "").upper() == sym_clean:
                return v
        return None

    def get_health(self) -> dict:
        return {
            "poll_count": self._poll_count,
            "last_poll_at": (
                self._last_poll_at.isoformat() if self._last_poll_at else None
            ),
            "live_signals": len(self._live),
            "evaluated_coins": len(self.get_scanned_coins()),
            "last_error": self._last_error,
            "healthy": self._last_error is None and self._poll_count > 0,
            "adaptive_interval_s": self.get_adaptive_poll_interval(),
        }

    def get_funnel_metrics(self) -> dict[str, Any]:
        """Return scanner funnel telemetry metrics."""
        counters = self._last_funnel_counters or {}
        cum = self._cumulative_funnel_counters or {}
        scanned = cum.get("scanned", 0) or counters.get("raw_universe", 0) or counters.get("scanned", 0)
        liq_rej = cum.get("liquidity_rejected", 0) or (counters.get("liquidity_rejected", 0) + counters.get("market_liquidity_rejected", 0))
        spread_rej = cum.get("spread_rejected", 0) or counters.get("spread_rejected", 0)
        mtf_rej = cum.get("mtf_rejected", 0) or counters.get("mtf_rejected", 0)
        exhaust_rej = cum.get("exhaustion_rejected", 0) or counters.get("exhaustion_rejected", 0)
        other_rej = cum.get("other_risk_rejected", 0) or (counters.get("other_risk_rejected", 0) + counters.get("pump_dump_rejected", 0))
        qualified = cum.get("qualified", 0) or counters.get("qualified", 0) or counters.get("c2_evaluated", 0)
        signals_gen = cum.get("signals_generated", 0) or counters.get("signals_generated", 0) or len(self._live)

        layers = [
            {
                "layer": 1,
                "name": "Total Scanned",
                "count": scanned,
                "conversion_pct": 100.0,
            },
            {
                "layer": 2,
                "name": "Liquidity & Turnover Gate",
                "count": max(0, scanned - liq_rej - spread_rej),
                "conversion_pct": round(max(0, scanned - liq_rej - spread_rej) / scanned * 100.0, 1) if scanned > 0 else 0.0,
            },
            {
                "layer": 3,
                "name": "MTF & Indicator Alignment",
                "count": max(0, scanned - liq_rej - spread_rej - mtf_rej - exhaust_rej),
                "conversion_pct": round(max(0, scanned - liq_rej - spread_rej - mtf_rej - exhaust_rej) / scanned * 100.0, 1) if scanned > 0 else 0.0,
            },
            {
                "layer": 4,
                "name": "Confluence & Sentiment Clean",
                "count": qualified,
                "conversion_pct": round(qualified / scanned * 100.0, 1) if scanned > 0 else 0.0,
            },
            {
                "layer": 5,
                "name": "Dispatched Signals",
                "count": signals_gen,
                "conversion_pct": round(signals_gen / scanned * 100.0, 1) if scanned > 0 else 0.0,
            },
        ]

        return {
            "scanned": scanned,
            "liquidity_rejected": liq_rej,
            "spread_rejected": spread_rej,
            "mtf_rejected": mtf_rej,
            "exhaustion_rejected": exhaust_rej,
            "other_risk_rejected": other_rej,
            "qualified": qualified,
            "signals_generated": signals_gen,
            "raw_signals_count": scanned,
            "pre_filtered_count": max(0, scanned - liq_rej - spread_rej),
            "confluence_passed_count": qualified,
            "ai_approved_count": qualified,
            "executed_count": signals_gen,
            "pre_filter_conversion_pct": round(max(0, scanned - liq_rej - spread_rej) / scanned * 100.0, 1) if scanned > 0 else 0.0,
            "confluence_conversion_pct": round(qualified / scanned * 100.0, 1) if scanned > 0 else 0.0,
            "ai_conversion_pct": round(qualified / scanned * 100.0, 1) if scanned > 0 else 0.0,
            "execution_conversion_pct": round(signals_gen / scanned * 100.0, 1) if scanned > 0 else 0.0,
            "layers": layers,
            "dispatched_signals_count": signals_gen,
            "final_conversion_pct": round(signals_gen / scanned * 100.0, 1) if scanned > 0 else 0.0,
            "latest": dict(counters),
            "cumulative": dict(cum),
        }

    def get_adaptive_poll_interval(self, context: dict | None = None) -> int:
        """Choose a bounded scan cadence from current market conditions.

        Clean bullish conditions can be checked more frequently; sideways and
        risk-off conditions slow the public-data poll to reduce noise and
        request pressure. The configured interval remains the neutral baseline.
        """
        base = max(15, int(self._config.scanner_poll_interval))
        sentiment = context or self._market_context_service.get_current_sentiment()
        regime = str(sentiment.get("market_regime", "RISK_ON")).upper()
        btc_trend = str(sentiment.get("btc_trend", "SIDEWAYS")).upper()
        if regime == "RISK_OFF" or btc_trend == "BEARISH":
            return min(300, max(base, int(base * 1.5)))
        if regime == "RISK_ON" and btc_trend == "BULLISH":
            return max(15, int(base * 0.75))
        return base

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _fetch_candidate_signals(
        self, max_timestamp_ms: int | None = None
    ) -> list[dict]:
        """Generate candidate signals natively from cached/fetched CoinDCX candles."""
        return await self._generate_native_candidates(max_timestamp_ms=max_timestamp_ms)

    async def _fetch_v1_signals(
        self, max_timestamp_ms: int | None = None
    ) -> list[dict]:
        """Candidate signals provider with support for method overrides.

        The historical cut-off (``max_timestamp_ms``) MUST reach candidate
        generation; dropping it makes replays/backtests score the latest
        candles at every historical step (look-ahead bias) and stamps
        signals with wall-clock time so simulated expiry never fires.
        """
        fetch_fn = getattr(self, "_fetch_candidate_signals", None)
        if fetch_fn is None or fetch_fn == self._fetch_v1_signals:
            return await self._generate_native_candidates(
                max_timestamp_ms=max_timestamp_ms
            )
        # Overrides (tests/plugins) may not accept the cut-off argument.
        try:
            params = inspect.signature(fetch_fn).parameters
            accepts_ts = "max_timestamp_ms" in params or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
            )
        except (TypeError, ValueError):
            accepts_ts = False
        if accepts_ts:
            return await fetch_fn(max_timestamp_ms=max_timestamp_ms)
        return await fetch_fn()

    fetch_candidate_signals = _fetch_candidate_signals

    async def _generate_native_candidates(
        self,
        max_timestamp_ms: int | None = None,
        coins: list[str] | None = None,
        canonical_inr_coins: set[str] | None = None,
    ) -> list[dict]:
        """
        Generate candidate signals natively with:
          - B2: Pre-C2 5-Stage Filter Cascade with Funnel Counters
          - B1: Top 50 Composite Ranking (Volume 0.40, Liquidity 0.35, Volatility 0.25)
        """
        if coins is None:
            coins = await self._fetch_watchlist_coins()
        discovery_metrics = dict(self._last_funnel_counters)
        if canonical_inr_coins is None:
            canonical_inr_coins = {
            "BTC",
            "ETH",
            "SOL",
            "BNB",
            "XRP",
            "ZEC",
            "AVAX",
            "LINK",
            "DOGE",
            "SHIB",
            "MATIC",
            "POL",
            "ADA",
            "TRX",
            "NEAR",
            "FET",
            "LTC",
            "DASH",
        }

        funnel_counters = {
            "scanned": 0,
            "raw_universe": 0,
            "market_universe": discovery_metrics.get("market_universe", 0),
            "liquidity_rejected": 0,
            "spread_rejected": 0,
            "pump_dump_rejected": 0,
            "mtf_rejected": 0,
            "exhaustion_rejected": 0,
            "other_risk_rejected": 0,
            "qualified": 0,
            "signals_generated": 0,
            "market_liquidity_rejected": discovery_metrics.get(
                "market_liquidity_rejected", 0
            ),
            "market_top_n": discovery_metrics.get("market_top_n", len(coins)),
            "liquidity_passed": 0,
            "volume_passed": 0,
            "pump_dump_passed": 0,
            "trend_aligned_passed": 0,
            "volatility_passed": 0,
            "c2_evaluated": 0,
        }

        candidates: list[dict] = []

        # Configured thresholds
        min_vol_24h = float(getattr(self._config, "scanner_min_24h_volume", 200000.0) or 200000.0)
        max_spread_pct = float(getattr(self._config, "scanner_max_spread_pct", 1.0) or 1.0)
        max_price_change_pct = float(getattr(
            self._config, "scanner_max_price_change_pct", 25.0
        ) or 25.0)
        min_atr_pct = float(getattr(self._config, "scanner_min_atr_pct", 0.5) or 0.5)
        max_atr_pct = float(getattr(self._config, "scanner_max_atr_pct", 12.0) or 12.0)
        w_vol = getattr(self._config, "scanner_ranking_weight_volume", 0.40)
        w_liq = getattr(self._config, "scanner_ranking_weight_liquidity", 0.35)
        w_atr = getattr(self._config, "scanner_ranking_weight_volatility", 0.25)
        top_n = getattr(self._config, "scanner_ranking_top_n", 50)

        for coin in coins:
            funnel_counters["raw_universe"] += 1
            if ":" in coin:
                pair = canonical_to_coindcx_pair(coin)
                coin_upper = pair.split(":", 1)[1]
            elif "/" in coin:
                base, quote = coin.upper().split("/", 1)
                coin_upper = base
                pair = f"{coin_upper}/{quote}"
            else:
                coin_upper = coin.upper()
                quote = "INR" if coin_upper in canonical_inr_coins else "USDT"
                pair = f"{coin_upper}/{quote}"

            candles = await self._get_candles_for_pair(
                pair, "1h", limit=120, max_timestamp_ms=max_timestamp_ms
            )

            if not candles:
                continue

            closes: list[float] = []
            highs: list[float] = []
            lows: list[float] = []
            volumes: list[float] = []

            for c in candles:
                try:
                    cl = float(c.get("close", c.get("c", 0.0)))
                    hi = float(c.get("high", c.get("h", cl)))
                    lo = float(c.get("low", c.get("l", cl)))
                    vo = float(c.get("volume", c.get("v", 0.0)))
                    if cl > 0:
                        closes.append(cl)
                        highs.append(hi)
                        lows.append(lo)
                        volumes.append(vo)
                except (ValueError, TypeError):
                    continue

            if not closes or len(closes) < 21:
                continue

            ticker = self._market_snapshot.get(pair, {})
            ticker_turnover = float(ticker.get("turnover") or 0.0)
            ticker_spread = float(ticker.get("spread_pct") or 0.0)

            # Stage 1: Liquidity & Spread Market Quality Gate
            if ticker_turnover and ticker_turnover < min_vol_24h:
                funnel_counters["liquidity_rejected"] += 1
                funnel_counters["market_liquidity_rejected"] += 1
                logger.debug(
                    "Hard rejection: %s LOW_TURNOVER (%.2f < %.2f)",
                    pair, ticker_turnover, min_vol_24h
                )
                continue

            if ticker_spread and ticker_spread > max_spread_pct:
                funnel_counters["spread_rejected"] += 1
                funnel_counters["liquidity_rejected"] += 1
                logger.debug(
                    "Hard rejection: %s HIGH_SPREAD (%.2f%% > %.2f%%)",
                    pair, ticker_spread, max_spread_pct
                )
                continue

            # Stage 1: Liquidity Floor passed. This is a real rejection gate:
            # candidates without a usable price/volume profile never reach C2.
            funnel_counters["liquidity_passed"] += 1

            latest_close = closes[-1]
            latest_high = highs[-1] if highs else latest_close
            vol_24h = ticker_turnover or (
                sum(volumes) * latest_close if volumes else 0.0
            )

            # Stage 2: 24h Volume Floor
            # If in testing or live, enforce floor unless dataset is micro-scale simulation
            if vol_24h > 0 and vol_24h < min_vol_24h and len(volumes) > 20:
                funnel_counters["liquidity_rejected"] += 1
                logger.debug(
                    "Hard rejection: %s LOW_TURNOVER (%.2f < %.2f)",
                    pair, vol_24h, min_vol_24h
                )
                continue
            funnel_counters["volume_passed"] += 1

            # Stage 3: Max price change % (Pump/Dump protection)
            price_change_24h_pct = (
                ((latest_close - closes[0]) / closes[0]) * 100.0
                if closes[0] > 0
                else 0.0
            )
            if abs(price_change_24h_pct) > max_price_change_pct:
                continue
            funnel_counters["pump_dump_passed"] += 1

            # Authoritative MTF set is 15m / 1h / 1d.  The 4h candles above
            # provide the middle timeframe; fetch the other two consistently.
            candles_1h_aux = await self._get_candles_for_pair(
                pair, "15m", limit=120, max_timestamp_ms=max_timestamp_ms
            )
            candles_1d_aux = await self._get_candles_for_pair(
                pair, "1d", limit=120, max_timestamp_ms=max_timestamp_ms
            )

            # Coin class determination
            if coin_upper in ("BTC", "ETH", "SOL", "BNB"):
                coin_class = "A"
            elif coin_upper in ("XRP", "ADA", "MATIC", "LINK", "AVAX", "DOGE"):
                coin_class = "B"
            else:
                coin_class = "C"

            rsi = 50.0
            volume_ratio = 1.0

            if len(closes) >= 21:
                ema9_list = calculate_ema(closes, 9)
                ema21_list = calculate_ema(closes, 21)
                ema9 = ema9_list[-1] if ema9_list else latest_close
                ema21 = ema21_list[-1] if ema21_list else latest_close

                # RSI 14
                if len(closes) >= 15:
                    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
                    gains = [d if d > 0 else 0.0 for d in deltas]
                    losses = [-d if d < 0 else 0.0 for d in deltas]
                    avg_g = sum(gains[-14:]) / 14.0
                    avg_l = sum(losses[-14:]) / 14.0
                    if avg_l > 0:
                        rs = avg_g / avg_l
                        rsi = 100.0 - (100.0 / (1.0 + rs))
                    elif avg_g > 0:
                        rsi = 100.0

                # Volume ratio
                if volumes:
                    avg_vol = sum(volumes[-20:]) / max(1, len(volumes[-20:]))
                    volume_ratio = (
                        round(volumes[-1] / avg_vol, 2) if avg_vol > 0 else 1.0
                    )

                # Stage 4: 15m / 1h / 1d trend & MTF alignment
                is_4h_bullish = ema9 >= ema21 * 0.995

                def _is_bullish(candle_set: list[dict]) -> bool:
                    values = [
                        float(c.get("close", c.get("c", 0.0)))
                        for c in candle_set
                        if float(c.get("close", c.get("c", 0.0))) > 0
                    ]
                    # Missing auxiliary timeframe data is unknown, not a
                    # bearish signal. The payload records completeness so
                    # operators can distinguish confirmed alignment from a
                    # candle-feed warm-up.
                    if len(values) < 5:
                        return True
                    fast = calculate_ema(values, min(9, len(values)))[-1]
                    slow = calculate_ema(values, min(21, len(values)))[-1]
                    return values[-1] >= fast * 0.99 and fast >= slow * 0.995

                is_1h_aligned = _is_bullish(candles_1h_aux)
                is_1d_aligned = _is_bullish(candles_1d_aux)
                mtf_aligned = bool(is_1h_aligned and is_4h_bullish and is_1d_aligned)

                if not mtf_aligned:
                    # Filter out coins with complete downtrend breakdown
                    if ema9 < ema21 * 0.97:
                        continue
                funnel_counters["trend_aligned_passed"] += 1

                # Stage 5: ATR volatility sanity band (0.5% - 12.0%)
                atr_val = sum(
                    (highs[i] - lows[i]) for i in range(-min(14, len(highs)), 0)
                ) / max(1, min(14, len(highs)))
                atr_pct = (atr_val / latest_close) * 100.0 if latest_close > 0 else 1.0
                if atr_pct < min_atr_pct or atr_pct > max_atr_pct:
                    continue
                funnel_counters["volatility_passed"] += 1

                # Market state determination
                recent_high = max(highs[-10:-1]) if len(highs) >= 10 else latest_high
                if ema9 >= ema21:
                    if latest_close >= recent_high or (
                        (latest_close - ema9) / ema9 > 0.005
                    ):
                        market_state = MarketState.BREAKOUT.value
                    elif latest_close >= ema9:
                        market_state = MarketState.BULL_TREND.value
                    else:
                        market_state = MarketState.PULLBACK.value
                else:
                    if latest_close > ema9:
                        market_state = MarketState.RECOVERY.value
                    else:
                        market_state = MarketState.DOWNTREND.value

                # Bot Archetype & Opportunity Type selection
                recent_range = (
                    (max(highs[-5:]) - min(lows[-5:])) if len(highs) >= 5 else 1.0
                )
                wider_range = (
                    (max(highs[-15:]) - min(lows[-15:])) if len(highs) >= 15 else 1.0
                )
                is_vcp = wider_range > 0 and (recent_range / wider_range) < 0.45

                # Bollinger Bands (20, 2) & Keltner Channels (20, 1.5 ATR) Squeeze detection
                is_bbs = False
                if len(closes) >= 20 and atr_val > 0:
                    c_window = closes[-20:]
                    c_mean = sum(c_window) / 20.0
                    c_std = (sum((x - c_mean) ** 2 for x in c_window) / 20.0) ** 0.5
                    bb_up = c_mean + 2.0 * c_std
                    bb_lo = c_mean - 2.0 * c_std
                    kc_up = c_mean + 1.5 * atr_val
                    kc_lo = c_mean - 1.5 * atr_val
                    is_squeeze = (bb_up <= kc_up) and (bb_lo >= kc_lo)
                    is_bbs = is_squeeze or (c_std > 0 and (2.0 * c_std / c_mean) < 0.035 and latest_close >= c_mean)

                # Donchian Channel (20-period) Breakout & Trend Momentum detection (MTB)
                is_mtb = False
                if len(highs) >= 21 and len(closes) >= 21:
                    donchian_high_20 = max(highs[-21:-1])
                    is_mtb = (latest_close > donchian_high_20) and (ema9 >= ema21) and is_4h_bullish

                # Narrow Range 7 (NR7) Squeeze & Expansion detection
                is_nr7 = False
                if len(highs) >= 8 and len(lows) >= 8:
                    ranges = [highs[i] - lows[i] for i in range(-8, -1)]
                    prior_range = ranges[-1]
                    prior_was_nr7 = prior_range <= min(ranges)
                    prior_high = highs[-2]
                    is_nr7 = prior_was_nr7 and (latest_close > prior_high) and (latest_close >= ema21) and is_4h_bullish

                # Pocket Pivot Accumulation (PPA) detection
                is_ppa = False
                if len(closes) >= 11 and len(volumes) >= 11:
                    down_volumes = [
                        volumes[i]
                        for i in range(-11, -1)
                        if closes[i] < closes[i - 1]
                    ]
                    max_down_vol = max(down_volumes) if down_volumes else 0.0
                    is_up_candle = latest_close > closes[-2]
                    is_ppa = is_up_candle and (volumes[-1] > max_down_vol) and (latest_close >= ema21) and is_4h_bullish

                # Mean Reversion Bounce (MRB) detection
                is_mrb = False
                if len(closes) >= 15:
                    is_oversold = (rsi <= 38.0) or (len(closes) >= 20 and c_std > 0 and latest_close <= (c_mean - 1.8 * c_std))
                    is_bounce = latest_close > closes[-2]
                    is_mrb = is_oversold and is_bounce and (market_state in (MarketState.PULLBACK.value, MarketState.RECOVERY.value, MarketState.BULL_TREND.value) or is_4h_bullish)

                if volume_ratio >= 1.5:
                    bot = "HDA"
                    opp_type = "absorption"
                    strategy_name = "High Delivery Absorption"
                elif is_bbs and is_4h_bullish:
                    bot = "BBS"
                    opp_type = "squeeze"
                    strategy_name = "Bollinger Band Squeeze Breakout"
                elif is_mtb:
                    bot = "MTB"
                    opp_type = "trend_breakout"
                    strategy_name = "Momentum Trend Breakout"
                elif is_nr7:
                    bot = "NR7"
                    opp_type = "narrow_range_expansion"
                    strategy_name = "Narrow Range 7 Squeeze"
                elif is_ppa:
                    bot = "PPA"
                    opp_type = "pocket_pivot"
                    strategy_name = "Pocket Pivot Accumulation"
                elif is_mrb:
                    bot = "MRB"
                    opp_type = "mean_reversion"
                    strategy_name = "Mean Reversion Bounce"
                elif is_vcp and is_4h_bullish:
                    bot = "VCP"
                    opp_type = "contraction"
                    strategy_name = "Volatility Contraction Pattern"
                elif market_state == MarketState.BREAKOUT.value:
                    bot = "STE"
                    opp_type = "momentum_trade"
                    strategy_name = "SuperTrend ATR Range Expansion"
                else:
                    bot = "STE"
                    opp_type = "continuation"
                    strategy_name = "SuperTrend ATR Range Expansion"

                # Phase 3A: Strict Multi-Timeframe Gate for Breakout Candidates
                if market_state == MarketState.BREAKOUT.value or opp_type == "momentum_trade":
                    if not mtf_aligned:
                        funnel_counters["mtf_rejected"] += 1
                        logger.debug(
                            "Hard rejection: %s MTF_MISMATCH for breakout (15m=%s, 1h=%s, 1d=%s)",
                            pair, is_1h_aligned, is_4h_bullish, is_1d_aligned,
                        )
                        continue

                # Candidate technical score calculation
                score = 75.0
                if ema9 >= ema21 > 0:
                    spread_ratio = (ema9 - ema21) / ema21
                    score += min(15.0, spread_ratio * 500)
                if 45 <= rsi <= 70:
                    score += 5.0
                if market_state in (
                    MarketState.BREAKOUT.value,
                    MarketState.BULL_TREND.value,
                ):
                    score += 5.0
                if mtf_aligned:
                    score += 5.0
                score = min(95.0, max(50.0, score))
            else:
                funnel_counters["trend_aligned_passed"] += 1
                funnel_counters["volatility_passed"] += 1
                market_state = MarketState.SIDEWAYS.value
                opp_type = "watchlist"
                bot = "STE"
                strategy_name = "SuperTrend ATR Range Expansion"
                mtf_aligned = False
                score = 65.0
                atr_pct = 2.0

            # B1 Composite Ranking Score
            # Weights: 0.40 * Volume_Ratio + 0.35 * Liquidity_Depth + 0.25 * ATR_Volatility
            norm_vol = min(100.0, volume_ratio * 30.0)
            # Liquidity & Depth: 24h INR turnover scale (benchmarked to ₹200k) + direct INR spot liquidity
            turnover_inr = vol_24h
            turnover_score = (
                min(100.0, (turnover_inr / 200000.0) * 100.0)
                if turnover_inr > 0
                else 50.0
            )
            pair_liquidity = 100.0 if quote == "INR" else 75.0
            norm_liq = (0.60 * turnover_score) + (0.40 * pair_liquidity)
            norm_atr = min(100.0, (atr_pct / 5.0) * 100.0)
            composite_rank_score = (
                (w_vol * norm_vol) + (w_liq * norm_liq) + (w_atr * norm_atr)
            )

            # Calculate realistic empirical Take Profit (+5% to +10%) & volatility-buffered Stop Loss (+4.5% to +7.5%)
            if bot == "HDA":
                if score >= 95.0:
                    tp_pct = 9.5
                elif score >= 90.0:
                    tp_pct = 7.0
                else:
                    tp_pct = 5.0
                sl_pct = min(7.5, max(4.5, atr_pct * 1.8)) if atr_pct > 0 else 4.5
            elif bot == "VCP":
                if score >= 95.0:
                    tp_pct = 10.0
                elif score >= 90.0:
                    tp_pct = 7.2
                else:
                    tp_pct = 5.0
                sl_pct = min(7.5, max(4.5, atr_pct * 1.8)) if atr_pct > 0 else 4.8
            elif bot == "BBS":
                if score >= 95.0:
                    tp_pct = 10.0
                elif score >= 90.0:
                    tp_pct = 7.5
                else:
                    tp_pct = 5.0
                sl_pct = min(7.5, max(4.5, atr_pct * 1.8)) if atr_pct > 0 else 4.5
            elif bot == "MTB":
                if score >= 95.0:
                    tp_pct = 9.5
                elif score >= 90.0:
                    tp_pct = 6.8
                else:
                    tp_pct = 4.8
                sl_pct = min(7.5, max(4.5, atr_pct * 1.8)) if atr_pct > 0 else 4.5
            elif bot == "NR7":
                if score >= 95.0:
                    tp_pct = 8.5
                elif score >= 90.0:
                    tp_pct = 6.5
                else:
                    tp_pct = 4.5
                sl_pct = min(7.5, max(4.5, atr_pct * 1.8)) if atr_pct > 0 else 4.5
            elif bot == "PPA":
                if score >= 95.0:
                    tp_pct = 9.5
                elif score >= 90.0:
                    tp_pct = 7.0
                else:
                    tp_pct = 4.8
                sl_pct = min(7.5, max(4.5, atr_pct * 1.8)) if atr_pct > 0 else 4.5
            elif bot == "MRB":
                if score >= 95.0:
                    tp_pct = 8.5
                elif score >= 90.0:
                    tp_pct = 6.5
                else:
                    tp_pct = 4.5
                sl_pct = min(7.5, max(4.5, atr_pct * 1.8)) if atr_pct > 0 else 4.5
            else:
                if score >= 95.0:
                    tp_pct = 9.0
                elif score >= 90.0:
                    tp_pct = 7.0
                else:
                    tp_pct = 4.8
                sl_pct = min(7.5, max(4.5, atr_pct * 1.8)) if atr_pct > 0 else 4.5

            # Ensure take profit satisfies minimum risk/reward ratio (>= 1.25) against buffered stop loss
            min_tp_for_rr = round(sl_pct * 1.25, 2)
            if tp_pct < min_tp_for_rr:
                tp_pct = min_tp_for_rr

            take_profit_px = (
                round(latest_close * (1.0 + tp_pct / 100.0), 8)
                if latest_close > 0
                else 0.0
            )
            stop_loss_px = (
                round(latest_close * (1.0 - sl_pct / 100.0), 8)
                if latest_close > 0
                else 0.0
            )

            cand_indicators = {
                "rsi": round(rsi, 2),
                "atr_pct": round(atr_pct, 2),
                "volume_ratio": round(volume_ratio, 2),
                "volume_24h": round(vol_24h, 2),
                "ema9": round(ema9, 4) if ema9 else 0.0,
                "ema21": round(ema21, 4) if ema21 else 0.0,
                "mtf_aligned": mtf_aligned,
            }
            signal_reason = (
                f"{strategy_name}: {opp_type} (score={score:.1f}, "
                f"RSI={rsi:.1f}, Vol={volume_ratio:.2f}x, ATR={atr_pct:.2f}%)"
            )

            candidates.append(
                {
                    "coin": coin_upper,
                    "pair": pair,
                    "score": round(score, 1),
                    "composite_score": round(composite_rank_score, 2),
                    "price": latest_close,
                    "entry_price": latest_close,
                    "stop_loss": stop_loss_px,
                    "take_profit": take_profit_px,
                    "sl_pct": round(sl_pct, 2),
                    "tp_pct": round(tp_pct, 2),
                    "signal_reason": signal_reason,
                    "indicators": cand_indicators,
                    "priority": (
                        "Super Elite"
                        if score >= 95
                        else ("Elite" if score >= 90 else ("High" if score >= 80 else "Medium"))
                    ),
                    "strategy": bot,
                    "strategy_name": strategy_name,
                    "timeframe": "1h",
                    "ttl_seconds": 300,
                    "mtf_timeframes": ["15m", "1h", "1d"],
                    "market_state": market_state,
                    "opportunity_type": opp_type,
                    "coin_class": coin_class,
                    "mtf_alignment": mtf_aligned,
                    "is_mtf_aligned": mtf_aligned,
                    "bot": bot,
                    "rsi": round(rsi, 2),
                    "atr_pct": round(atr_pct, 2),
                    "volume_24h": round(vol_24h, 2),
                    "volume_ratio": round(volume_ratio, 2),
                    "timestamp": datetime.fromtimestamp(
                        max_timestamp_ms / 1000.0, tz=timezone.utc
                    ).isoformat()
                    if max_timestamp_ms
                    else None,
                }
            )

        # B1: Rank by composite score descending and cap at top N (default 50)
        candidates.sort(key=lambda c: c.get("composite_score", 0.0), reverse=True)
        candidates = candidates[:top_n]

        funnel_counters["c2_evaluated"] = len(candidates)
        self._last_funnel_counters = funnel_counters
        logger.info(
            "Scanner Funnel Cascade: raw=%d, liq=%d, vol=%d, pump_dump=%d, trend=%d, atr=%d -> c2_eval=%d",
            funnel_counters["raw_universe"],
            funnel_counters["liquidity_passed"],
            funnel_counters["volume_passed"],
            funnel_counters["pump_dump_passed"],
            funnel_counters["trend_aligned_passed"],
            funnel_counters["volatility_passed"],
            funnel_counters["c2_evaluated"],
            extra={"funnel": funnel_counters},
        )

        return candidates

    async def _publish_signal_generated(self, sig: Signal) -> None:
        raw_p = sig.raw_payload or {}
        price = float(raw_p.get("price") or raw_p.get("entry_price") or raw_p.get("close") or 0.0)
        if price <= 0.0:
            logger.warning("Scanner skipping signal %s for %s: price is %.8f", sig.id, sig.coin, price)
            return

        bot = (
            sig.source_bot
            if sig.source_bot in ("STE", "HDA", "VCP", "BBS", "MTB", "NR7")
            else raw_p.get("bot", "STE")
        )
        strategy = raw_p.get("strategy") or bot
        strat_name = raw_p.get("strategy_name") or (
            "High Delivery Absorption"
            if strategy == "HDA"
            else (
                "Volatility Contraction Pattern"
                if strategy == "VCP"
                else (
                    "Bollinger Band Squeeze Breakout"
                    if strategy == "BBS"
                    else (
                        "Momentum Trend Breakout"
                        if strategy == "MTB"
                        else (
                            "Narrow Range 7 Squeeze"
                            if strategy == "NR7"
                            else "SuperTrend ATR Range Expansion"
                        )
                    )
                )
            )
        )
        ttl_seconds = (
            int((sig.expires_at - sig.generated_at).total_seconds())
            if sig.expires_at and sig.generated_at
            else 300
        )
        payload = {
            "id": sig.id,
            "signal_id": sig.id,
            "strategy": strategy,
            "strategy_name": strat_name,
            "mode": sig.mode.value if hasattr(sig.mode, "value") else str(sig.mode),
            "coin": sig.coin,
            "pair": sig.pair,
            "priority": sig.priority.value,
            "score": sig.score,
            "confidence": sig.confidence,
            "price": price,
            "entry_price": price,
            "entry": price,
            "stop_loss": raw_p.get("stop_loss"),
            "take_profit": raw_p.get("take_profit"),
            "signal_reason": raw_p.get("signal_reason") or f"{strategy} signal generated",
            "indicators": raw_p.get("indicators") or {},
            "ttl_seconds": ttl_seconds,
            "market_state": sig.market_state.value,
            "opportunity_type": sig.opportunity_type.value,
            "bot": bot,
            "coin_class": sig.coin_class,
            "timeframe": raw_p.get("timeframe", "1h"),
            "timestamp": sig.generated_at.isoformat(),
            "expires_at": sig.expires_at.isoformat(),
            "source": "scanner_service",
            "confluence": sig.confluence_breakdown or {},
            "confluence_score": (sig.raw_payload or {}).get("confluence_score"),
            "dynamic_threshold": (sig.raw_payload or {}).get("dynamic_threshold"),
            "mtf_timeframes": (sig.raw_payload or {}).get(
                "mtf_timeframes", ["15m", "1h", "1d"]
            ),
            "ai_eligible": True,
            "raw_payload": raw_p,
        }
        await self._bus.publish(EventType.SIGNAL_GENERATED, payload)
        await self._event_log.append(
            event_type=EventType.SIGNAL_GENERATED.value,
            payload=payload,
            source_service="scanner_service",
            entity_id=sig.id,
        )

    async def _publish_signal_expired(self, sig: Signal) -> None:
        payload = {
            "signal_id": sig.id,
            "coin": sig.coin,
            "reason": "TTL",
        }
        await self._bus.publish(EventType.SIGNAL_EXPIRED, payload)
        await self._event_log.append(
            event_type=EventType.SIGNAL_EXPIRED.value,
            payload=payload,
            source_service="scanner_service",
            entity_id=sig.id,
        )
