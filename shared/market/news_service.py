"""Past 30-day financial news sentiment and risk scraper for Indian equities.

Scrapes official financial media feeds (Yahoo Finance & Google News RSS) for NSE symbols
to safeguard swing positions against SEBI regulatory actions, fraud, or earnings disasters.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import time
from typing import Any
import xml.etree.ElementTree as ET

import httpx

from core.logging import get_logger

logger = get_logger("market.news_service")

NEGATIVE_KEYWORDS: list[str] = [
    "sebi",
    "penalty",
    "fraud",
    "scam",
    "raid",
    "ed raid",
    "investigation",
    "loss widen",
    "downgrade",
    "default",
    "resignation",
    "pledge",
    "npa",
    "violation",
    "cbi",
    "inquiry",
    "ban",
    "litigation",
    "fir",
    "bribe",
]

POSITIVE_KEYWORDS: list[str] = [
    "profit",
    "growth",
    "order win",
    "dividend",
    "upgrade",
    "outperform",
    "expansion",
    "surge",
    "acquisition",
    "bonus",
    "fii",
    "strong",
    "record",
    "patent",
    "capacity",
]


class NewsSentimentService:
    """Async news risk evaluator with 1-hour in-memory cache."""

    def __init__(self, cache_ttl_seconds: int = 3600, timeout: float = 6.0) -> None:
        self.cache_ttl = cache_ttl_seconds
        self.timeout = timeout
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                headers={"User-Agent": "Mozilla/5.0 Project-Beta/1.0"},
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def evaluate_symbol(self, symbol: str) -> dict[str, Any]:
        """Fetch past 30 days headlines and score risk: SAFE, POSITIVE, CAUTION, HIGH_RISK."""
        clean = symbol.upper().replace("NSE:", "").replace("BSE:", "").replace(".NS", "").replace(".BO", "").strip()
        now = time.monotonic()

        # Check cache
        if clean in self._cache:
            ts, cached = self._cache[clean]
            if now - ts < self.cache_ttl:
                return cached

        headlines = await self._fetch_headlines(clean)
        status, score, flags, top_headline = self._score_headlines(headlines)

        result = {
            "symbol": clean,
            "status": status,
            "score": score,
            "flags": flags,
            "headline": top_headline,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

        self._cache[clean] = (now, result)
        return result

    async def _fetch_headlines(self, symbol: str) -> list[str]:
        headlines: list[str] = []
        client = await self._get_client()

        # 1. Yahoo Finance Search News API
        try:
            url = f"https://query1.finance.yahoo.com/v1/finance/search?q={symbol}.NS&newsCount=5"
            resp = await client.get(url)
            if resp.status_code == 200:
                for item in (resp.json().get("news") or []):
                    title = item.get("title")
                    if title:
                        headlines.append(title)
        except Exception as e:
            logger.debug("Yahoo news fetch failed for %s: %s", symbol, e)

        # 2. Google News RSS for India (fallback if < 3 headlines)
        if len(headlines) < 3:
            try:
                rss_url = f"https://news.google.com/rss/search?q={symbol}+share+price+NSE+when:30d&hl=en-IN&gl=IN&ceid=IN:en"
                resp = await client.get(rss_url)
                if resp.status_code == 200:
                    root = ET.fromstring(resp.text)
                    for item in root.findall(".//item")[:5]:
                        t = item.find("title")
                        if t is not None and t.text:
                            headlines.append(t.text)
            except Exception as e:
                logger.debug("Google news RSS fetch failed for %s: %s", symbol, e)

        return headlines[:6]

    @staticmethod
    def _score_headlines(headlines: list[str]) -> tuple[str, int, list[str], str]:
        if not headlines:
            return "UNKNOWN", 0, [], "No verifiable headlines returned"

        score = 0
        flags: list[str] = []
        top_headline = headlines[0]

        for h in headlines:
            h_lower = h.lower()
            for neg in NEGATIVE_KEYWORDS:
                if neg in h_lower:
                    score -= 2
                    flags.append(f"Risk flag: '{neg}' in '{h[:50]}...'")
            for pos in POSITIVE_KEYWORDS:
                if pos in h_lower:
                    score += 1

        if score <= -4:
            status = "HIGH_RISK"
        elif score < 0 or flags:
            status = "CAUTION"
        elif score > 0:
            status = "POSITIVE"
        else:
            status = "SAFE"

        return status, score, flags, top_headline
