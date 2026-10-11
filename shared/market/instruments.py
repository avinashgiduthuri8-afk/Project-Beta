"""Dhan public compact-instrument master, filtered to NSE/BSE cash equity."""

from __future__ import annotations

import csv
import inspect
from io import StringIO
from typing import Awaitable, Callable

import httpx

from .symbols import StockSymbol

INSTRUMENT_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"


class DhanInstrumentMaster:
    def __init__(self, fetcher: Callable[[], str | Awaitable[str]] | None = None) -> None:
        self._fetcher = fetcher
        self._symbols: dict[str, StockSymbol] = {}

    async def refresh(self) -> None:
        if self._fetcher:
            result = self._fetcher()
            text = await result if inspect.isawaitable(result) else result
        else:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(INSTRUMENT_URL)
                response.raise_for_status()
                text = response.text
        parsed: dict[str, StockSymbol] = {}
        for row in csv.DictReader(StringIO(text)):
            exchange = (row.get("SEM_EXM_EXCH_ID") or row.get("EXCH_ID") or "").upper()
            segment = (row.get("SEM_SEGMENT") or row.get("SEGMENT") or "").upper()
            instrument = (row.get("SEM_INSTRUMENT_NAME") or row.get("INSTRUMENT") or "").upper()
            if exchange not in {"NSE", "BSE"} or segment not in {"E", "EQ", "C"} or instrument not in {"EQUITY", "EQ"}:
                continue
            trading_symbol = row.get("SEM_TRADING_SYMBOL") or row.get("SYMBOL_NAME") or ""
            security_id = row.get("SEM_SMST_SECURITY_ID") or row.get("SECURITY_ID")
            symbol = StockSymbol(exchange, trading_symbol, security_id)
            parsed[symbol.canonical] = symbol
        self._symbols = parsed

    def resolve(self, symbol: StockSymbol) -> StockSymbol:
        return self._symbols[symbol.canonical]
