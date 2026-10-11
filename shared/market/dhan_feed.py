"""State and safety boundary for Dhan's market-data WebSocket."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from .models import MarketQuote
from .symbols import StockSymbol


class DhanMarketFeed:
    """Market-data-only feed. It intentionally exposes no order operations."""

    def __init__(self, client_id: str, access_token: str) -> None:
        if not client_id or not access_token:
            raise ValueError("Dhan market data credentials are required")
        self.client_id = client_id
        self.access_token = access_token
        self.generation = 0
        self.running = False
        self._quotes: dict[str, MarketQuote] = {}
        self._symbols: tuple[StockSymbol, ...] = ()
        self._callback: Callable[[MarketQuote], Awaitable[None]] | None = None

    async def start(self, symbols: Iterable[StockSymbol], on_quote: Callable[[MarketQuote], Awaitable[None]]) -> None:
        self._symbols = tuple(symbols)
        self._callback = on_quote
        self.running = True

    async def stop(self) -> None:
        self.running = False
        self._callback = None
        self._quotes.clear()

    def accept_quote(self, quote: MarketQuote) -> bool:
        if quote.generation != self.generation:
            return False
        self._quotes[quote.symbol.canonical] = quote
        return True

    def latest(self, symbol: StockSymbol) -> MarketQuote | None:
        quote = self._quotes.get(symbol.canonical)
        return quote if quote and quote.generation == self.generation else None

    def mark_disconnected(self) -> None:
        self.generation += 1
        self._quotes.clear()

    def health(self) -> dict[str, Any]:
        return {"healthy": self.running, "generation": self.generation, "symbols": len(self._symbols), "quotes": len(self._quotes)}
