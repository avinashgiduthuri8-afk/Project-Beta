"""Public, keyless Indian-equity market data client.

Yahoo Finance's public chart endpoint is used for delayed NSE/BSE quotes and
OHLCV candles.  It is suitable for research and paper trading; broker-authenticated
feeds must be used for live execution.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any, Iterable

import httpx

from core.logging import get_logger

if TYPE_CHECKING:
    from market.symbols import StockSymbol

logger = get_logger("market.indian_public_client")

DEFAULT_NSE_SYMBOLS = (
    "RELIANCE",
    "HDFCBANK",
    "TCS",
    "BHARTIARTL",
    "ICICIBANK",
    "SBIN",
    "INFY",
    "HINDUNILVR",
    "ITC",
    "LT",
    "KOTAKBANK",
    "AXISBANK",
    "BAJFINANCE",
    "MARUTI",
    "SUNPHARMA",
    "TATAMOTORS",
    "TITAN",
    "NTPC",
    "ONGC",
    "POWERGRID",
)


class TokenBucketRateLimiter:
    """Small async token bucket used to avoid hammering a public endpoint."""

    def __init__(self, max_rate_per_sec: float = 2.0, burst: int = 2) -> None:
        self._max_rate = max(0.1, max_rate_per_sec)
        self._capacity = max(1, burst)
        self._tokens = float(self._capacity)
        self._last_update = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_update
            self._last_update = now
            self._tokens = min(
                self._capacity, self._tokens + elapsed * self._max_rate
            )
            if self._tokens < 1.0:
                await asyncio.sleep((1.0 - self._tokens) / self._max_rate)
                self._tokens = 0.0
                self._last_update = time.monotonic()
            else:
                self._tokens -= 1.0


class IndianStockPublicClient:
    """Async adapter exposing keyless NSE/BSE data in Alpha's market shape."""

    BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
    INTERVAL_RANGES = {
        "1m": ("1m", "7d"),
        "5m": ("5m", "60d"),
        "15m": ("15m", "60d"),
        "30m": ("30m", "60d"),
        "1h": ("60m", "2y"),
        "1d": ("1d", "5y"),
        "1wk": ("1wk", "10y"),
    }

    def __init__(
        self,
        timeout: float = 12.0,
        rate_limit_per_sec: float = 2.0,
        max_retries: int = 2,
        symbols: Iterable[str] | None = None,
        exchange: str = "NSE",
    ) -> None:
        self._timeout = timeout
        self._max_retries = max_retries
        self._rate_limiter = TokenBucketRateLimiter(rate_limit_per_sec)
        self._client: httpx.AsyncClient | None = None
        self.exchange = exchange.upper()
        self.symbols = tuple(self.normalize_symbol(s)[1] for s in (symbols or DEFAULT_NSE_SYMBOLS))

    async def __aenter__(self) -> "IndianStockPublicClient":
        self._get_client()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                headers={"User-Agent": "Mozilla/5.0 Project-Beta/1.0"},
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    @staticmethod
    def normalize_symbol(symbol: str | StockSymbol, default_exchange: str = "NSE") -> tuple[str, str]:
        """Return ``(exchange, tradingsymbol)`` from common broker/display forms."""
        if hasattr(symbol, "trading_symbol") and hasattr(symbol, "exchange"):
            return str(symbol.exchange).upper(), str(symbol.trading_symbol).upper()
        clean = str(symbol).strip().upper().replace("/INR", "")
        if clean.startswith("NSE:"):
            return "NSE", clean.split(":", 1)[1]
        if clean.startswith("BSE:"):
            return "BSE", clean.split(":", 1)[1]
        if clean.endswith(".NS"):
            return "NSE", clean[:-3]
        if clean.endswith(".BO"):
            return "BSE", clean[:-3]
        return default_exchange.upper(), clean

    @classmethod
    def provider_symbol(cls, symbol: str | StockSymbol, default_exchange: str = "NSE") -> str:
        exchange, trading_symbol = cls.normalize_symbol(symbol, default_exchange)
        if trading_symbol.startswith("^"):
            return trading_symbol
        return f"{trading_symbol}.{'BO' if exchange == 'BSE' else 'NS'}"

    async def _request_chart(
        self, symbol: str, interval: str, range_value: str
    ) -> dict[str, Any]:
        url = f"{self.BASE_URL}/{self.provider_symbol(symbol, self.exchange)}"
        params = {
            "interval": interval,
            "range": range_value,
            "includePrePost": "false",
            "events": "div,splits",
        }
        for attempt in range(self._max_retries + 1):
            await self._rate_limiter.acquire()
            try:
                response = await self._get_client().get(url, params=params)
                response.raise_for_status()
                payload = response.json()
                result = ((payload.get("chart") or {}).get("result") or [])
                if result:
                    return result[0]
                error = (payload.get("chart") or {}).get("error")
                raise ValueError(f"No chart data for {symbol}: {error}")
            except (httpx.HTTPError, ValueError) as exc:
                if attempt >= self._max_retries:
                    raise
                logger.warning(
                    "Public market request failed; retrying",
                    extra={"symbol": symbol, "attempt": attempt + 1, "error": str(exc)},
                )
                await asyncio.sleep(float(attempt + 1))
        return {}

    async def get_candles(
        self, pair: str, interval: str = "15m", limit: int = 120
    ) -> list[dict[str, Any]]:
        provider_interval, range_value = self.INTERVAL_RANGES.get(
            interval, (interval, "1y")
        )
        chart = await self._request_chart(pair, provider_interval, range_value)
        timestamps = chart.get("timestamp") or []
        quote_sets = ((chart.get("indicators") or {}).get("quote") or [{}])
        quotes = quote_sets[0] if quote_sets else {}
        exchange, trading_symbol = self.normalize_symbol(pair, self.exchange)
        canonical = f"{exchange}:{trading_symbol}"
        candles: list[dict[str, Any]] = []
        for index, timestamp in enumerate(timestamps):
            try:
                values = {
                    name: (quotes.get(name) or [])[index]
                    for name in ("open", "high", "low", "close", "volume")
                }
                if any(values[name] is None for name in ("open", "high", "low", "close")):
                    continue
                candles.append(
                    {
                        "pair": canonical,
                        "timeframe": interval,
                        "timestamp": int(timestamp) * 1000,
                        "time": int(timestamp) * 1000,
                        "open": float(values["open"]),
                        "high": float(values["high"]),
                        "low": float(values["low"]),
                        "close": float(values["close"]),
                        "volume": float(values["volume"] or 0.0),
                    }
                )
            except (IndexError, TypeError, ValueError):
                continue
        return candles[-max(1, int(limit)) :]

    async def get_quote(self, symbol: str) -> dict[str, Any]:
        chart = await self._request_chart(symbol, "1d", "5d")
        meta = chart.get("meta") or {}
        candles = await self._chart_to_latest(chart)
        exchange, trading_symbol = self.normalize_symbol(symbol, self.exchange)
        last = float(meta.get("regularMarketPrice") or candles.get("close") or 0.0)
        previous = float(meta.get("chartPreviousClose") or meta.get("previousClose") or last)
        volume = float(meta.get("regularMarketVolume") or candles.get("volume") or 0.0)
        change_pct = ((last - previous) / previous * 100.0) if previous > 0 else 0.0
        return {
            "market": f"{exchange}:{trading_symbol}",
            "symbol": trading_symbol,
            "exchange": exchange,
            "last_price": last,
            "bid": last,
            "ask": last,
            "high": float(meta.get("regularMarketDayHigh") or candles.get("high") or last),
            "low": float(meta.get("regularMarketDayLow") or candles.get("low") or last),
            "volume": volume,
            "turnover": volume * last,
            "change_24_hour": change_pct,
            "timestamp": int(meta.get("regularMarketTime") or time.time()),
            "data_delay_note": "Public feed may be delayed; not for live execution",
        }

    @staticmethod
    async def _chart_to_latest(chart: dict[str, Any]) -> dict[str, float]:
        quote_sets = ((chart.get("indicators") or {}).get("quote") or [{}])
        quotes = quote_sets[0] if quote_sets else {}
        closes = quotes.get("close") or []
        index = next((i for i in range(len(closes) - 1, -1, -1) if closes[i] is not None), None)
        if index is None:
            return {}
        result: dict[str, float] = {}
        for name in ("open", "high", "low", "close", "volume"):
            values = quotes.get(name) or []
            if index < len(values) and values[index] is not None:
                result[name] = float(values[index])
        return result

    async def get_tickers(self) -> list[dict[str, Any]]:
        results = await asyncio.gather(
            *(self.get_quote(symbol) for symbol in self.symbols),
            return_exceptions=True,
        )
        tickers: list[dict[str, Any]] = []
        for symbol, result in zip(self.symbols, results):
            if isinstance(result, Exception):
                logger.debug("Quote unavailable", extra={"symbol": symbol, "error": str(result)})
            elif result.get("last_price", 0.0) > 0:
                tickers.append(result)
        return tickers

    async def get_orderbook(self, pair: str) -> dict[str, Any]:
        quote = await self.get_quote(pair)
        last = float(quote.get("last_price") or 0.0)
        return {
            "pair": quote.get("market"),
            "bids": {},
            "asks": {},
            "best_bid": last,
            "best_ask": last,
            "spread": 0.0,
            "spread_pct": 0.0,
            "timestamp": quote.get("timestamp", int(time.time())),
            "depth_available": False,
        }


# Compatibility name for unchanged callers. It now resolves to Indian equities,
