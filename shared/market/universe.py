"""NSE cash-equity universe values and deterministic eligibility filtering."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Iterable

from .symbols import StockSymbol


@dataclass(frozen=True, slots=True)
class UniverseInstrument:
    symbol: StockSymbol
    series: str
    instrument_type: str
    sector: str
    tick_size: Decimal
    trading_status: str
    is_sme: bool
    surveillance_status: str
    price_band_pct: Decimal
    circuit_locked_sessions: int = 0


class NseUniverse:
    def __init__(self, loader: Callable[[], Iterable[UniverseInstrument]]) -> None:
        self.loader = loader

    def refresh(self) -> tuple[UniverseInstrument, ...]:
        return tuple(
            item
            for item in self.loader()
            if item.symbol.exchange == "NSE"
            and item.series == "EQ"
            and item.instrument_type == "EQUITY"
            and item.trading_status == "ACTIVE"
            and not item.is_sme
            and item.surveillance_status == "NONE"
        )
