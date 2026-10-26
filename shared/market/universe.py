"""Indian cash-equity universe values and deterministic eligibility filtering."""

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


class CashEquityUniverse:
    def __init__(
        self,
        loader: Callable[[], Iterable[UniverseInstrument]],
        exchanges: Iterable[str] = ("NSE", "BSE"),
    ) -> None:
        self.loader = loader
        self.exchanges = frozenset(str(exchange).upper() for exchange in exchanges)

    def refresh(self) -> tuple[UniverseInstrument, ...]:
        return tuple(
            item
            for item in self.loader()
            if item.symbol.exchange in self.exchanges
            and item.series == "EQ"
            and item.instrument_type == "EQUITY"
            and item.trading_status == "ACTIVE"
            and not item.is_sme
            and item.surveillance_status == "NONE"
        )


class NseUniverse(CashEquityUniverse):
    def __init__(self, loader: Callable[[], Iterable[UniverseInstrument]]) -> None:
        super().__init__(loader, exchanges=("NSE",))
