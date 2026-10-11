"""State and safety boundary for Dhan's market-data WebSocket."""

from __future__ import annotations

import asyncio
import json
import struct
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import websockets

from .models import MarketQuote
from .symbols import StockSymbol


class DhanMarketFeed:
    """Market-data-only feed. It intentionally exposes no order operations."""

    def __init__(self, client_id: str, access_token: str, websocket_factory=None) -> None:
        if not client_id or not access_token:
            raise ValueError("Dhan market data credentials are required")
        self.client_id = client_id
        self.access_token = access_token
        self.generation = 0
        self.running = False
        self._quotes: dict[str, MarketQuote] = {}
        self._symbols: tuple[StockSymbol, ...] = ()
        self._callback: Callable[[MarketQuote], Awaitable[None]] | None = None
        self._websocket_factory = websocket_factory or websockets.connect
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self, symbols: Iterable[StockSymbol], on_quote: Callable[[MarketQuote], Awaitable[None]]) -> None:
        self._symbols = tuple(symbols)
        self._callback = on_quote
        self.running = True
        self._stop.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self.running = False
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._callback = None
        self._quotes.clear()

    async def _run(self) -> None:
        delay = 1.0
        while not self._stop.is_set():
            try:
                await self._consume_connection()
                delay = 1.0
            except asyncio.CancelledError:
                raise
            except Exception:
                self.mark_disconnected()
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)

    async def _consume_connection(self) -> None:
        query = urlencode({"version": "2", "token": self.access_token, "clientId": self.client_id, "authType": "2"})
        async with self._websocket_factory(f"wss://api-feed.dhan.co?{query}", ping_interval=10, ping_timeout=40) as websocket:
            instruments = [
                {"ExchangeSegment": f"{symbol.exchange}_EQ", "SecurityId": str(symbol.security_id)}
                for symbol in self._symbols if symbol.security_id
            ]
            for offset in range(0, len(instruments), 100):
                batch = instruments[offset:offset + 100]
                await websocket.send(json.dumps({"RequestCode": 15, "InstrumentCount": len(batch), "InstrumentList": batch}))
            async for packet in websocket:
                quote = self._parse_ticker(packet)
                if quote and self.accept_quote(quote) and self._callback:
                    await self._callback(quote)

    def _parse_ticker(self, packet: bytes | str) -> MarketQuote | None:
        if not isinstance(packet, bytes) or len(packet) < 16 or packet[0] != 2:
            return None
        security_id = str(struct.unpack_from("<I", packet, 4)[0])
        symbol = next((item for item in self._symbols if item.security_id == security_id), None)
        if not symbol:
            return None
        price = Decimal(str(struct.unpack_from("<f", packet, 8)[0]))
        epoch = struct.unpack_from("<I", packet, 12)[0]
        return MarketQuote(symbol, price, datetime.fromtimestamp(epoch, timezone.utc), "DHAN", self.generation)

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
