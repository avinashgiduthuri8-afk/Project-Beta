from datetime import datetime, timezone
from decimal import Decimal

import aiosqlite
import pytest

from core.repository.beta_store import BetaStore
from market.models import MarketQuote
from market.symbols import StockSymbol
from scanner.indian_service import IndianScannerService
from scanner.models import RankingEvidence, ScannerCandidate
from scanner.ranking import CandidateRanker
from shared.strategies.base import StrategySignal


def test_candidate_ranker_is_deterministic_and_limits_deep_scan_to_fifty():
    candidates = [
        ScannerCandidate(
            StockSymbol.parse(f"NSE:S{i:03d}"), "TEST", False,
            datetime(2026, 10, 16, tzinfo=timezone.utc),
            RankingEvidence(Decimal(i % 5), Decimal("1"), Decimal(i), Decimal(i), Decimal("1"), Decimal("1"), Decimal("1")),
            Decimal("1000"),
        )
        for i in range(60)
    ]
    first = CandidateRanker().rank(candidates, {"regime": "BULL"})
    second = CandidateRanker().rank(list(reversed(candidates)), {"regime": "BULL"})
    assert len(first) == 50
    assert [item.symbol.canonical for item in first] == [item.symbol.canonical for item in second]
    assert first[0].ranking_score >= first[-1].ranking_score


@pytest.mark.asyncio
async def test_scanner_ranks_all_survivors_but_deep_evaluates_only_fifty():
    now = datetime(2026, 10, 16, 5, 0, tzinfo=timezone.utc)
    symbols = tuple(StockSymbol.parse(f"NSE:S{i:03d}") for i in range(60))

    class Provider:
        async def get_quote(self, symbol):
            return MarketQuote(symbol, Decimal("100"), now, "TEST", 0)

        async def get_candles(self, symbol, timeframe, limit):
            offset = int(symbol.trading_symbol[-3:]) / 100
            return [
                {"timestamp": int(now.timestamp() * 1000) + i * 60_000,
                 "open": 90 + i + offset, "high": 92 + i + offset,
                 "low": 89 + i + offset, "close": 91 + i + offset,
                 "volume": 1_000_000}
                for i in range(20)
            ]

    class CountingStrategy:
        def __init__(self):
            self.calls = 0

        def evaluate(self, df, symbol):
            self.calls += 1
            price = Decimal(str(df.iloc[-1]["close"]))
            return StrategySignal("TEST", symbol, price, price - 1, price + 2, 90)

    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    store = BetaStore(conn)
    await store.initialize()
    strategy = CountingStrategy()
    scanner = IndianScannerService(
        Provider(), store, symbols, clock=lambda: now,
        strategies={"TEST": strategy}, min_atr_pct=0,
    )

    report = await scanner.poll()

    assert strategy.calls == 10  # publication cap stops further expensive work
    assert report["qualified"] == 10
    async with conn.execute("SELECT COUNT(*) FROM beta_scanner_rankings") as cursor:
        assert (await cursor.fetchone())[0] == 50
    async with conn.execute("SELECT COUNT(*) FROM beta_scanner_decisions WHERE code='QUALIFIED'") as cursor:
        assert (await cursor.fetchone())[0] == 10
    await conn.close()
