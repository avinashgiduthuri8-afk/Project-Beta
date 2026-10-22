"""TradingView public and authenticated client for Indian equities.

Leverages TradingView's high-speed India equities screener endpoint for real-time
quotes, technical indicator aggregations, and sub-₹250 universe scanning.
Requires no credentials by default (keyless public mode) while optionally supporting
user-supplied TradingView API keys / session tokens.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import time
from typing import TYPE_CHECKING, Any, Iterable

import httpx

from core.logging import get_logger

if TYPE_CHECKING:
    from market.symbols import StockSymbol

logger = get_logger("market.tradingview_client")


class TradingViewPublicClient:
    """Async client for TradingView India equities screener & quote endpoints."""

    BASE_URL = "https://scanner.tradingview.com/india/scan"

    COLUMNS = [
        "name",
        "close",
        "change",
        "open",
        "high",
        "low",
        "volume",
        "Recommend.All",
        "RSI",
        "EMA20",
        "SMA20",
        "ATR",
        "description",
    ]

    def __init__(
        self,
        api_key: str | None = None,
        timeout: float = 12.0,
        rate_limit_per_sec: float = 3.0,
        max_retries: int = 2,
    ) -> None:
        self.api_key = api_key.strip() if api_key and api_key.strip() else None
        self._timeout = timeout
        self._max_retries = max_retries
        self._client: httpx.AsyncClient | None = None
        self._last_call = 0.0
        self._min_interval = 1.0 / max(0.1, rate_limit_per_sec)
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> "TradingViewPublicClient":
        self._get_client()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Accept": "application/json",
            }
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                headers=headers,
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    @staticmethod
    def normalize_symbol(symbol: str | StockSymbol, default_exchange: str = "NSE") -> tuple[str, str]:
        """Return (exchange, trading_symbol) normalized for TradingView."""
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
    def provider_ticker(cls, symbol: str | StockSymbol, default_exchange: str = "NSE") -> str:
        """Format symbol as TradingView ticker string, e.g. 'NSE:TATASTEEL'."""
        exchange, trading_symbol = cls.normalize_symbol(symbol, default_exchange)
        return f"{exchange}:{trading_symbol}"

    async def _rate_limit(self) -> None:
        async with self._lock:
            elapsed = time.monotonic() - self._last_call
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            self._last_call = time.monotonic()

    async def _post_scan(self, payload: dict[str, Any]) -> dict[str, Any]:
        await self._rate_limit()
        client = self._get_client()
        for attempt in range(self._max_retries + 1):
            try:
                resp = await client.post(self.BASE_URL, json=payload)
                if resp.status_code == 200:
                    return resp.json()
                logger.warning("TradingView scanner returned status %s: %s", resp.status_code, resp.text[:200])
            except Exception as exc:
                if attempt >= self._max_retries:
                    logger.error("TradingView scanner request failed after %s attempts: %s", attempt + 1, exc)
                    raise
                await asyncio.sleep(0.5 * (attempt + 1))
        return {}

    def _parse_row(self, row: dict[str, Any], columns: list[str]) -> dict[str, Any]:
        ticker = row.get("s", "")
        data = row.get("d", [])
        mapped = dict(zip(columns, data))

        close_val = mapped.get("close")
        if close_val is None:
            raise ValueError(f"TradingView row for {ticker} missing close price")

        last_price = Decimal(str(close_val))
        return {
            "ticker": ticker,
            "name": mapped.get("name") or ticker.split(":")[-1],
            "last_price": last_price,
            "change": mapped.get("change"),
            "open": mapped.get("open"),
            "high": mapped.get("high"),
            "low": mapped.get("low"),
            "volume": mapped.get("volume") or 0,
            "recommendation": mapped.get("Recommend.All"),
            "rsi": mapped.get("RSI"),
            "ema20": mapped.get("EMA20"),
            "sma20": mapped.get("SMA20"),
            "atr": mapped.get("ATR"),
            "description": mapped.get("description") or "",
            "timestamp": datetime.now(timezone.utc),
            "source": "TRADINGVIEW",
        }

    async def get_quote(self, symbol: str | StockSymbol) -> dict[str, Any]:
        """Fetch real-time snapshot quote for a single symbol."""
        ticker = self.provider_ticker(symbol)
        quotes = await self.get_quotes([symbol])
        if ticker in quotes:
            return quotes[ticker]
        canonical = f"{self.normalize_symbol(symbol)[0]}:{self.normalize_symbol(symbol)[1]}"
        if canonical in quotes:
            return quotes[canonical]
        raise ValueError(f"Symbol {ticker} not found in TradingView response")

    async def get_quotes(self, symbols: Iterable[str | StockSymbol]) -> dict[str, dict[str, Any]]:
        """Batch-fetch snapshot quotes for multiple symbols in a single round-trip."""
        ticker_list = [self.provider_ticker(s) for s in symbols]
        if not ticker_list:
            return {}

        payload = {
            "symbols": {"tickers": ticker_list},
            "columns": self.COLUMNS,
        }
        res = await self._post_scan(payload)
        out: dict[str, dict[str, Any]] = {}
        for row in res.get("data", []):
            try:
                parsed = self._parse_row(row, self.COLUMNS)
                out[parsed["ticker"]] = parsed
            except Exception as exc:
                logger.debug("Skipping unparseable row %s: %s", row, exc)
        return out

    async def scan_market(
        self,
        exchange: str = "NSE",
        price_floor: float = 1.0,
        price_ceiling: float = 250.0,
        min_volume: int = 500_000,
        limit: int = 50,
        sort_by: str = "volume",
        sort_order: str = "desc",
    ) -> list[dict[str, Any]]:
        """Screen the Indian equities market for liquid candidates matching price and volume bounds."""
        payload = {
            "filter": [
                {"left": "exchange", "operation": "equal", "right": exchange.upper()},
                {"left": "close", "operation": "in_range", "right": [price_floor, price_ceiling]},
                {"left": "volume", "operation": "greater", "right": min_volume},
            ],
            "options": {"lang": "en"},
            "symbols": {"query": {"types": []}, "tickers": []},
            "columns": self.COLUMNS,
            "sort": {"sortBy": sort_by, "sortOrder": sort_order},
            "range": [0, max(1, limit)],
        }
        res = await self._post_scan(payload)
        candidates: list[dict[str, Any]] = []
        for row in res.get("data", []):
            try:
                candidates.append(self._parse_row(row, self.COLUMNS))
            except Exception as exc:
                logger.debug("Skipping invalid screener row: %s", exc)
        return candidates
