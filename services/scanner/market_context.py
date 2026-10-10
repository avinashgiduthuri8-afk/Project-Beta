"""NIFTY and BANK NIFTY market-regime context for Indian equities."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from shared.market.public_client import IndianStockPublicClient
from shared.market.session import is_stock_market_open


def calculate_ema(prices: list[float], period: int) -> list[float]:
    if len(prices) < period:
        return []
    multiplier = 2.0 / (period + 1)
    values = [sum(prices[:period]) / period]
    for price in prices[period:]:
        values.append((price - values[-1]) * multiplier + values[-1])
    return values


def determine_trend_from_candles(candles: list[dict[str, Any]]) -> str:
    closes = [float(c.get("close") or c.get("c") or 0.0) for c in candles]
    closes = [value for value in closes if value > 0]
    if len(closes) < 21:
        return "SIDEWAYS"
    fast = calculate_ema(closes, 9)[-1]
    slow = calculate_ema(closes, 21)[-1]
    difference = (fast - slow) / slow if slow else 0.0
    if difference > 0.0015 and closes[-1] >= fast:
        return "BULLISH"
    if difference < -0.0015 and closes[-1] <= fast:
        return "BEARISH"
    return "SIDEWAYS"


class MarketContextService:
    """Tracks Indian benchmark trend while retaining legacy context keys."""

    def __init__(self, timeout_seconds: float = 8.0) -> None:
        self._client = IndianStockPublicClient(timeout=timeout_seconds)
        self._latest_context: dict[str, Any] = {
            "nifty_trend": "SIDEWAYS",
            "bank_nifty_trend": "SIDEWAYS",
            "market_regime": "RISK_OFF",
            "market_open": False,
            "market_sentiment": 50,
            # Compatibility keys consumed by the cloned confluence engine.
            "btc_trend": "SIDEWAYS",
            "eth_trend": "SIDEWAYS",
            "fear_and_greed": 50,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def evaluate_regime(
        self,
        nifty_candles: list[dict[str, Any]],
        bank_nifty_candles: list[dict[str, Any]],
    ) -> tuple[str, str, str]:
        nifty = determine_trend_from_candles(nifty_candles)
        bank = determine_trend_from_candles(bank_nifty_candles)
        regime = "RISK_ON" if nifty == "BULLISH" and bank != "BEARISH" else "RISK_OFF"
        return nifty, bank, regime

    async def fetch_pair_candles(
        self, pair: str, interval: str = "15m", limit: int = 30
    ) -> list[dict[str, Any]]:
        return await self._client.get_candles(pair, interval=interval, limit=limit)

    async def fetch_fear_and_greed(self) -> int:
        return int(self._latest_context.get("market_sentiment", 50))

    async def refresh_market_context(self) -> dict[str, Any]:
        nifty_data = await self.fetch_pair_candles("^NSEI", "15m", 60)
        bank_data = await self.fetch_pair_candles("^NSEBANK", "15m", 60)
        nifty, bank, regime = self.evaluate_regime(nifty_data, bank_data)
        sentiment = 65 if regime == "RISK_ON" else 35
        self._latest_context = {
            "nifty_trend": nifty,
            "bank_nifty_trend": bank,
            "market_regime": regime,
            "market_open": is_stock_market_open(),
            "market_sentiment": sentiment,
            "nifty_price": float(nifty_data[-1]["close"]) if nifty_data else 0.0,
            "bank_nifty_price": float(bank_data[-1]["close"]) if bank_data else 0.0,
            "btc_trend": nifty,
            "eth_trend": bank,
            "fear_and_greed": sentiment,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        return self._latest_context

    def get_current_sentiment(self) -> dict[str, Any]:
        return self._latest_context

    async def close(self) -> None:
        await self._client.close()
