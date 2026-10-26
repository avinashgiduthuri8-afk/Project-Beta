"""Fundamental-data normalization and opt-in ranking contracts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import aiosqlite
import pytest

from core.repository.beta_store import BetaStore
from market.fundamentals import FundamentalData
from market.models import MarketQuote
from market.symbols import StockSymbol
from scanner.indian_service import IndianScannerService
from scanner.models import RankingEvidence, ScannerCandidate
from scanner.ranking import CandidateRanker, FundamentalRankingWeights


NOW = datetime(2026, 10, 23, 6, 0, tzinfo=timezone.utc)


def candidate(symbol: str, fundamentals: FundamentalData | None = None) -> ScannerCandidate:
    return ScannerCandidate(
        symbol=StockSymbol.parse(symbol),
        sector="TEST",
        penny_stock=False,
        evidence_timestamp=NOW,
        evidence=RankingEvidence(
            liquidity=Decimal("10"),
            spread_quality=Decimal("5"),
            momentum=Decimal("4"),
            relative_strength=Decimal("3"),
            timeframe_alignment=Decimal("2"),
            volatility_suitability=Decimal("1"),
            data_quality=Decimal("5"),
        ),
        affordability_preview=Decimal("5"),
        fundamentals=fundamentals,
    )


def fundamentals(symbol: str, *, as_of: datetime = NOW, **overrides) -> FundamentalData:
    values = {
        "market_cap": "100000000000",
        "pe_ratio": "20",
        "eps": "12",
        "revenue_growth_pct": "15",
        "profit_growth_pct": "18",
        "debt_to_equity": "0.4",
        "roe_pct": "22",
    }
    values.update(overrides)
    return FundamentalData(
        symbol=StockSymbol.parse(symbol),
        as_of=as_of,
        source="TEST_SOURCE",
        **values,
    )


def test_fundamental_data_normalizes_supported_optional_fields():
    data = FundamentalData(
        symbol=StockSymbol.parse("NSE:RELIANCE"),
        as_of=NOW,
        source="TEST_SOURCE",
        market_cap="1000000.50",
        pe_ratio=20,
        eps=None,
        revenue_growth_pct="12.5",
        profit_growth_pct=None,
        debt_to_equity="0.25",
        roe_pct="18",
    )

    assert data.market_cap == Decimal("1000000.50")
    assert data.pe_ratio == Decimal("20")
    assert data.eps is None
    assert data.revenue_growth_pct == Decimal("12.5")
    assert data.is_fresh(NOW + timedelta(days=7), timedelta(days=7))
    assert not data.is_fresh(NOW + timedelta(days=8), timedelta(days=7))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("market_cap", "0"),
        ("pe_ratio", "-1"),
        ("eps", "NaN"),
        ("revenue_growth_pct", "Infinity"),
        ("debt_to_equity", "-0.1"),
        ("roe_pct", object()),
    ],
)
def test_invalid_fundamental_values_are_rejected(field, value):
    with pytest.raises(ValueError, match="INVALID_FUNDAMENTAL_DATA"):
        fundamentals("NSE:RELIANCE", **{field: value})


def test_fundamental_ranking_weights_validate_bounds_and_unknown_fields():
    assert not FundamentalRankingWeights().enabled
    assert FundamentalRankingWeights.from_mapping({"roe_pct": "10"}).enabled

    with pytest.raises(ValueError, match="INVALID_FUNDAMENTAL_WEIGHTS"):
        FundamentalRankingWeights.from_mapping({"roe_pct": -1})
    with pytest.raises(ValueError, match="INVALID_FUNDAMENTAL_WEIGHTS"):
        FundamentalRankingWeights.from_mapping({"roe_pct": "NaN"})
    with pytest.raises(ValueError, match="INVALID_FUNDAMENTAL_WEIGHTS"):
        FundamentalRankingWeights.from_mapping({"roe_pct": 60, "eps": 50})
    with pytest.raises(ValueError, match="INVALID_FUNDAMENTAL_WEIGHTS"):
        FundamentalRankingWeights.from_mapping({"unsupported": 1})


def test_fundamental_weights_rank_stronger_valid_data_higher():
    weaker = fundamentals(
        "NSE:AAA",
        market_cap="1000000",
        pe_ratio="40",
        eps="2",
        revenue_growth_pct="3",
        profit_growth_pct="2",
        debt_to_equity="1.5",
        roe_pct="5",
    )
    stronger = fundamentals("NSE:ZZZ")
    weights = FundamentalRankingWeights.from_mapping(
        {
            "market_cap": 2,
            "pe_ratio": 2,
            "eps": 2,
            "revenue_growth_pct": 2,
            "profit_growth_pct": 2,
            "debt_to_equity": 2,
            "roe_pct": 2,
        }
    )

    ranked = CandidateRanker(fundamental_weights=weights).rank(
        [candidate("NSE:AAA", weaker), candidate("NSE:ZZZ", stronger)],
        {"regime": "BULL"},
    )

    assert [item.symbol.canonical for item in ranked] == ["NSE:ZZZ", "NSE:AAA"]
    assert ranked[0].fundamental_score == Decimal("14")
    assert ranked[1].fundamental_score == Decimal("0")
    assert ranked[0].ranking_score == Decimal("44")


def test_missing_stale_and_non_positive_fundamentals_add_no_score():
    weights = FundamentalRankingWeights.from_mapping({"roe_pct": 10})
    stale = fundamentals("NSE:STALE", as_of=NOW - timedelta(days=8), roe_pct="30")
    negative = fundamentals("NSE:NEGATIVE", roe_pct="-5")
    valid = fundamentals("NSE:VALID", roe_pct="20")

    ranked = CandidateRanker(
        fundamental_weights=weights,
        fundamental_max_age=timedelta(days=7),
    ).rank(
        [
            candidate("NSE:MISSING"),
            candidate("NSE:STALE", stale),
            candidate("NSE:NEGATIVE", negative),
            candidate("NSE:VALID", valid),
        ],
        {"regime": "BULL"},
    )

    scores = {item.symbol.canonical: item.fundamental_score for item in ranked}
    assert scores == {
        "NSE:VALID": Decimal("10"),
        "NSE:MISSING": Decimal("0"),
        "NSE:NEGATIVE": Decimal("0"),
        "NSE:STALE": Decimal("0"),
    }


def test_default_ranker_preserves_technical_only_scores_and_order():
    candidates = [
        candidate("NSE:ZZZ", fundamentals("NSE:ZZZ", roe_pct="90")),
        candidate("NSE:AAA"),
    ]

    ranked = CandidateRanker().rank(candidates, {"regime": "BULL"})

    assert [item.symbol.canonical for item in ranked] == ["NSE:AAA", "NSE:ZZZ"]
    assert [item.ranking_score for item in ranked] == [Decimal("30"), Decimal("30")]
    assert [item.fundamental_score for item in ranked] == [Decimal("0"), Decimal("0")]


@pytest.mark.asyncio
async def test_scanner_fetches_fundamentals_and_persists_ranking_evidence():
    symbols = (StockSymbol.parse("NSE:AAA"), StockSymbol.parse("NSE:ZZZ"))

    class MarketProvider:
        async def get_quote(self, symbol):
            return MarketQuote(symbol, Decimal("100"), NOW, "TEST", 0)

        async def get_candles(self, symbol, timeframe, limit):
            return [
                {
                    "timestamp": int(NOW.timestamp() * 1000) + index * 60_000,
                    "open": 90 + index,
                    "high": 92 + index,
                    "low": 89 + index,
                    "close": 91 + index,
                    "volume": 1_000_000,
                }
                for index in range(20)
            ][-limit:]

    class FundamentalsProvider:
        async def get_fundamentals(self, symbol):
            roe = "20" if symbol.canonical == "NSE:ZZZ" else "5"
            return fundamentals(symbol.canonical, roe_pct=roe)

    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    store = BetaStore(conn)
    await store.initialize()
    try:
        scanner = IndianScannerService(
            MarketProvider(),
            store,
            symbols,
            clock=lambda: NOW,
            strategies={},
            min_atr_pct=0,
            fundamental_provider=FundamentalsProvider(),
            ranker=CandidateRanker(
                fundamental_weights=FundamentalRankingWeights.from_mapping({"roe_pct": 10})
            ),
        )

        await scanner.poll()

        async with conn.execute(
            "SELECT canonical_symbol, rank, evidence_json FROM beta_scanner_rankings ORDER BY rank"
        ) as cursor:
            rows = [dict(row) for row in await cursor.fetchall()]
        first_evidence = json.loads(rows[0]["evidence_json"])
        second_evidence = json.loads(rows[1]["evidence_json"])
        assert [(row["canonical_symbol"], row["rank"]) for row in rows] == [
            ("NSE:ZZZ", 1),
            ("NSE:AAA", 2),
        ]
        assert first_evidence["fundamental_score"] == "10"
        assert first_evidence["fundamentals"]["roe_pct"] == "20"
        assert second_evidence["fundamental_score"] == "0"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_unavailable_fundamental_provider_cannot_add_positive_score():
    symbol = StockSymbol.parse("NSE:AAA")

    class MarketProvider:
        async def get_quote(self, requested_symbol):
            return MarketQuote(requested_symbol, Decimal("100"), NOW, "TEST", 0)

        async def get_candles(self, requested_symbol, timeframe, limit):
            return [
                {
                    "timestamp": int(NOW.timestamp() * 1000) + index * 60_000,
                    "open": 90 + index,
                    "high": 92 + index,
                    "low": 89 + index,
                    "close": 91 + index,
                    "volume": 1_000_000,
                }
                for index in range(20)
            ][-limit:]

    class UnavailableFundamentals:
        async def get_fundamentals(self, requested_symbol):
            raise TimeoutError("fundamental source unavailable")

    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    store = BetaStore(conn)
    await store.initialize()
    try:
        scanner = IndianScannerService(
            MarketProvider(),
            store,
            [symbol],
            clock=lambda: NOW,
            strategies={},
            min_atr_pct=0,
            fundamental_provider=UnavailableFundamentals(),
            ranker=CandidateRanker(
                fundamental_weights=FundamentalRankingWeights.from_mapping({"roe_pct": 10})
            ),
        )

        await scanner.poll()

        async with conn.execute(
            "SELECT evidence_json FROM beta_scanner_rankings WHERE canonical_symbol='NSE:AAA'"
        ) as cursor:
            evidence = json.loads((await cursor.fetchone())[0])
        assert evidence["fundamental_score"] == "0"
        assert evidence["fundamentals"] is None
    finally:
        await conn.close()
