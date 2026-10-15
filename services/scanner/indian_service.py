"""Deterministic five-stage scanner for NSE/BSE cash equities."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
from typing import Callable, Iterable, Mapping

from core.repository.beta_store import BetaStore
from core.repository.quote_repo import QuoteRepository
from market.provider import MarketDataProvider
from market.session import BSE_SESSION, NSE_SESSION
from market.symbols import StockSymbol
from scanner.validation import MarketDataValidator
from shared.research.indicators import enrich_candles_df
from shared.strategies.base import BaseStockStrategy
from shared.strategies.registry import get_active_strategies


class IndianScannerService:
    """Owns market access, evaluates the 5 active strategies, and writes only qualified paper signals."""

    def __init__(
        self,
        provider: MarketDataProvider,
        store: BetaStore,
        symbols: Iterable[StockSymbol],
        *,
        clock: Callable[[], datetime] | None = None,
        max_quote_age: timedelta = timedelta(minutes=15),
        quote_repo: QuoteRepository | None = None,
        strategies: Mapping[str, BaseStockStrategy] | None = None,
    ) -> None:
        self.provider = provider
        self.store = store
        self.symbols = tuple(symbols)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.max_quote_age = max_quote_age
        self.quote_repo = quote_repo
        self.validator = MarketDataValidator()
        self.strategies = dict(strategies) if strategies is not None else get_active_strategies()

    async def poll(self, max_timestamp_ms: int | None = None) -> dict:
        stages = [1, 2, 3, 4, 5]
        result = {"stages": stages, "qualified": 0, "duplicates": 0, "rejections": []}
        now = self.clock()
        for symbol in self.symbols:
            # Stage 1: supported exchange and open cash-market session.
            session = NSE_SESSION if symbol.exchange == "NSE" else BSE_SESSION
            if not session.is_open(now):
                result["rejections"].append({"symbol": symbol.canonical, "stage": 1, "code": "MARKET_CLOSED"})
                continue

            # Stage 2: a current, positive provider quote.
            quote = await self.provider.get_quote(symbol)
            if self.quote_repo:
                await self.quote_repo.upsert(quote)
            if not quote.is_fresh(now, self.max_quote_age):
                result["rejections"].append({"symbol": symbol.canonical, "stage": 2, "code": "STALE_MARKET_DATA"})
                continue

            # Stage 3: structurally valid, liquid candles.
            candles = await self.provider.get_candles(symbol, "15m", 120)
            validated = self.validator.validate_series(candles, pair=symbol.canonical)
            turnover = sum(
                Decimal(str(c.get("close", 0))) * Decimal(str(c.get("volume", 0)))
                for c in validated.cleaned_candles[-20:]
            )
            if not validated.is_valid or len(validated.cleaned_candles) < 10 or turnover < Decimal("1000000"):
                result["rejections"].append({"symbol": symbol.canonical, "stage": 3, "code": "INVALID_OR_ILLIQUID_CANDLES"})
                continue

            # Stage 4: Strategy Evaluation across the 5 approved strategies (STE, HDA, VCP, BBS, NR7)
            df = enrich_candles_df(validated.cleaned_candles)
            triggered_signals = []
            for _, strat in self.strategies.items():
                sig = strat.evaluate(df, symbol)
                if sig and sig.score >= 85:
                    triggered_signals.append(sig)

            if not triggered_signals:
                result["rejections"].append({"symbol": symbol.canonical, "stage": 4, "code": "LOW_CONFLUENCE"})
                continue

            # Stage 5: deterministic expiry/idempotency boundary and persistence.
            bucket = int(quote.timestamp.timestamp()) // 900
            for sig in triggered_signals:
                signal_id = "sig-" + sha256(f"{symbol.canonical}:{sig.strategy_name}:{bucket}".encode()).hexdigest()[:20]
                inserted = await self.store.insert_signal_once(
                    signal_id,
                    symbol,
                    sig.strategy_name,
                    sig.score,
                    quote.price,
                    quote.timestamp.isoformat(),
                    quote.source,
                )
                if inserted:
                    result["qualified"] += 1
                    result["signal_id"] = signal_id
                else:
                    result["duplicates"] += 1

        return result
