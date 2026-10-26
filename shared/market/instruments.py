"""Dhan public compact-instrument master, filtered to NSE/BSE cash equity."""

from __future__ import annotations

import csv
import inspect
from io import StringIO
from typing import Awaitable, Callable

import httpx
from decimal import Decimal

from .symbols import StockSymbol
from .universe import CashEquityUniverse, NseUniverse, UniverseInstrument

INSTRUMENT_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"


class DhanInstrumentMaster:
    def __init__(self, fetcher: Callable[[], str | Awaitable[str]] | None = None) -> None:
        self._fetcher = fetcher
        self._symbols: dict[str, StockSymbol] = {}
        self._universe: dict[str, UniverseInstrument] = {}

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
        universe: dict[str, UniverseInstrument] = {}
        for row in csv.DictReader(StringIO(text)):
            exchange = (row.get("SEM_EXM_EXCH_ID") or row.get("EXCH_ID") or "").upper()
            segment = (row.get("SEM_SEGMENT") or row.get("SEGMENT") or "").upper()
            instrument = (row.get("SEM_INSTRUMENT_NAME") or row.get("INSTRUMENT") or "").upper()
            if exchange not in {"NSE", "BSE"} or segment not in {"E", "EQ", "C"} or instrument not in {"EQUITY", "EQ"}:
                continue
            trading_symbol = row.get("SEM_TRADING_SYMBOL") or row.get("SYMBOL_NAME") or ""
            security_id = row.get("SEM_SMST_SECURITY_ID") or row.get("SECURITY_ID")
            try:
                symbol = StockSymbol(exchange, trading_symbol, security_id)
            except ValueError:
                # Public masters can contain headers, placeholders, or symbols
                # outside Beta's canonical cash-equity grammar. Fail closed.
                continue
            parsed[symbol.canonical] = symbol
            series = (row.get("SEM_SERIES") or row.get("SERIES") or "EQ").upper()
            universe[symbol.canonical] = UniverseInstrument(
                symbol=symbol,
                series=series,
                instrument_type="EQUITY",
                sector=(row.get("SEM_SECTOR") or row.get("SECTOR") or "UNKNOWN").upper(),
                tick_size=Decimal(row.get("SEM_TICK_SIZE") or row.get("TICK_SIZE") or "0.05"),
                trading_status=(row.get("SEM_TRADING_STATUS") or row.get("TRADING_STATUS") or "ACTIVE").upper(),
                is_sme=series in {"SM", "ST"},
                surveillance_status="NONE",
                price_band_pct=Decimal(row.get("PRICE_BAND") or "20"),
            )
        self._symbols = parsed
        self._universe = universe

    def resolve(self, symbol: StockSymbol) -> StockSymbol:
        return self._symbols[symbol.canonical]

    def nse_universe(self) -> tuple[UniverseInstrument, ...]:
        return NseUniverse(lambda: self._universe.values()).refresh()

    def cash_equity_universe(self) -> tuple[UniverseInstrument, ...]:
        return CashEquityUniverse(lambda: self._universe.values()).refresh()
