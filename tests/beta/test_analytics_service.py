"""Read-only analytics contracts over existing Project Beta evidence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from analytics.service import AnalyticsFilter, AnalyticsService, BacktestRun
from backtesting.engine import BacktestResult, BacktestTrade, EquityPoint, ExitReason
from core.database import open_beta_database
from core.migrations import initialize_beta_schema
from market.symbols import StockSymbol


NOW = datetime(2026, 10, 23, 6, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def remove_analytics_database_artifacts(tmp_path):
    yield
    for path in tmp_path.glob("*.db*"):
        path.unlink(missing_ok=True)


async def _seed_database(path) -> None:
    conn = await open_beta_database(str(path))
    try:
        await initialize_beta_schema(conn)
        await conn.executemany(
            """INSERT INTO beta_signals
            (id,symbol,exchange,strategy,score,price,market_timestamp,source,
             stop_loss,take_profit,timeframe,sector,status)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    "sig-nse", "AAA", "NSE", "STE", 90, "100",
                    "2026-10-22T05:00:00+00:00", "TEST_NSE", "95", "110",
                    "15m", "IT", "QUALIFIED",
                ),
                (
                    "sig-bse", "BBB", "BSE", "VCP", 88, "200",
                    "2026-10-23T05:00:00+00:00", "TEST_BSE", None, None,
                    None, "UNKNOWN", "QUALIFIED",
                ),
            ],
        )
        await conn.executemany(
            """INSERT INTO beta_scanner_decisions
            (cycle_id,canonical_symbol,stage,code,message,evidence_json,created_at)
            VALUES(?,?,?,?,?,?,?)""",
            [
                (
                    "cycle-nse", "NSE:AAA", 8, "QUALIFIED", "qualified", "{}",
                    "2026-10-22T05:00:00+00:00",
                ),
                (
                    "cycle-bse", "BSE:BBB", 3, "STALE_MARKET_DATA", "stale", "{}",
                    "2026-10-23T05:00:00+00:00",
                ),
            ],
        )
        await conn.executemany(
            """INSERT INTO beta_scanner_rankings
            (cycle_id,canonical_symbol,rank,score,evidence_json) VALUES(?,?,?,?,?)""",
            [
                ("cycle-nse", "NSE:AAA", 1, "92", '{"momentum":"5"}'),
                ("cycle-bse", "BSE:BBB", 2, "87", '{"fundamentals":null}'),
            ],
        )
        await conn.executemany(
            """INSERT INTO beta_quotes
            (canonical_symbol,symbol,exchange,price,timestamp,source,generation)
            VALUES(?,?,?,?,?,?,?)""",
            [
                ("NSE:AAA", "AAA", "NSE", "101", "2026-10-22T05:00:00+00:00", "TEST_NSE", 1),
                ("BSE:BBB", "BBB", "BSE", "201", "2026-10-23T05:58:00+00:00", "TEST_BSE", 1),
            ],
        )
        await conn.commit()
    finally:
        await conn.close()


def _backtest_run() -> BacktestRun:
    symbol = StockSymbol.parse("NSE:AAA")
    trade = BacktestTrade(
        strategy="STE",
        symbol=symbol,
        signal_time=NOW - timedelta(days=2),
        entry_time=NOW - timedelta(days=2) + timedelta(minutes=5),
        exit_time=NOW - timedelta(days=1),
        quantity=10,
        entry_price=Decimal("100"),
        exit_price=Decimal("110"),
        stop_loss=Decimal("95"),
        take_profit=Decimal("110"),
        exit_reason=ExitReason.TAKE_PROFIT,
        gross_pnl=Decimal("100"),
        transaction_costs=Decimal("4"),
        slippage_costs=Decimal("1"),
        net_pnl=Decimal("95"),
    )
    result = BacktestResult(
        initial_capital=Decimal("10000"),
        final_equity=Decimal("10095"),
        trades=(trade,),
        equity_curve=(
            EquityPoint(NOW - timedelta(days=2), Decimal("10000"), Decimal("0")),
            EquityPoint(NOW - timedelta(days=1), Decimal("10095"), Decimal("0")),
        ),
        max_drawdown_pct=Decimal("0"),
        win_rate_pct=Decimal("100"),
        profit_factor=None,
        net_return_pct=Decimal("0.95"),
        total_transaction_costs=Decimal("4"),
        total_slippage_costs=Decimal("1"),
        invalid_candle_count=0,
        open_position_quantity=0,
    )
    return BacktestRun(
        run_id="run-1",
        market="INDIA",
        exchange="NSE",
        symbol="NSE:AAA",
        strategy="STE",
        started_at=NOW - timedelta(days=2),
        ended_at=NOW - timedelta(days=1),
        result=result,
    )


@pytest.mark.asyncio
async def test_snapshot_filters_persisted_evidence_and_marks_unsupported_fields(tmp_path):
    path = tmp_path / "beta.db"
    await _seed_database(path)
    service = AnalyticsService(str(path))

    snapshot = await service.snapshot(
        AnalyticsFilter(
            market="INDIA",
            exchange="BSE",
            symbol="BSE:BBB",
            strategy="VCP",
            start=NOW - timedelta(hours=2),
            end=NOW,
        ),
        now=NOW,
    )

    assert [row["id"] for row in snapshot["signals"]["items"]] == ["sig-bse"]
    signal = snapshot["signals"]["items"][0]
    assert signal["market"] == "INDIA"
    assert signal["unavailable_fields"] == ["stop_loss", "take_profit", "timeframe"]
    assert [row["symbol"] for row in snapshot["rankings"]["items"]] == ["BSE:BBB"]
    assert snapshot["rankings"]["unsupported_filters"] == ["strategy"]
    assert snapshot["rankings"]["items"][0]["strategy"] is None


@pytest.mark.asyncio
async def test_snapshot_reports_empty_and_unavailable_data_without_fabrication(tmp_path):
    path = tmp_path / "empty.db"
    conn = await open_beta_database(str(path))
    await initialize_beta_schema(conn)
    await conn.close()

    snapshot = await AnalyticsService(str(path)).snapshot(now=NOW)

    assert snapshot["signals"] == {"status": "AVAILABLE", "items": [], "reason": "NO_DATA"}
    assert snapshot["rankings"]["items"] == []
    assert snapshot["backtests"] == {
        "status": "UNAVAILABLE", "items": [], "reason": "NO_BACKTEST_RESULTS"
    }
    assert snapshot["health"]["market_data"]["status"] == "UNAVAILABLE"
    assert snapshot["health"]["scanner"]["status"] == "UNAVAILABLE"
    assert snapshot["health"]["providers"] == []


@pytest.mark.asyncio
async def test_available_signals_are_separate_from_complete_signal_history(tmp_path):
    path = tmp_path / "beta.db"
    await _seed_database(path)
    conn = await open_beta_database(str(path))
    await conn.execute("UPDATE beta_signals SET status='CONSUMED' WHERE id='sig-nse'")
    await conn.commit()
    await conn.close()

    snapshot = await AnalyticsService(str(path)).snapshot(now=NOW)

    assert [row["id"] for row in snapshot["signals"]["items"]] == ["sig-bse"]
    assert [row["id"] for row in snapshot["signal_history"]["items"]] == [
        "sig-nse", "sig-bse"
    ]


@pytest.mark.asyncio
async def test_backtest_analytics_reuses_result_metrics_and_exposes_curves_and_trades(tmp_path):
    path = tmp_path / "beta.db"
    await _seed_database(path)
    service = AnalyticsService(str(path), backtests=(_backtest_run(),))

    snapshot = await service.snapshot(
        AnalyticsFilter(exchange="NSE", symbol="NSE:AAA", strategy="STE"), now=NOW
    )

    backtest = snapshot["backtests"]["items"][0]
    assert backtest["summary"]["trade_count"] == 1
    assert backtest["summary"]["winning_trades"] == 1
    assert backtest["summary"]["losing_trades"] == 0
    assert backtest["summary"]["net_return_pct"] == "0.95"
    assert backtest["summary"]["profit_factor"] is None
    assert backtest["summary"]["unavailable_fields"] == ["profit_factor"]
    assert backtest["equity_curve"][1]["equity"] == "10095"
    assert backtest["trades"][0]["exit_reason"] == "TAKE_PROFIT"


@pytest.mark.asyncio
async def test_health_uses_observed_freshness_and_scanner_errors(tmp_path):
    path = tmp_path / "beta.db"
    await _seed_database(path)

    health = (await AnalyticsService(str(path)).snapshot(now=NOW))["health"]

    assert health["market_data"]["status"] == "FRESH"
    assert health["market_data"]["latest_observation"] == "2026-10-23T05:58:00+00:00"
    assert health["scanner"]["status"] == "DEGRADED"
    assert health["scanner"]["error_count"] == 1
    assert health["scanner"]["latest_errors"][0]["code"] == "STALE_MARKET_DATA"
    providers = {row["source"]: row for row in health["providers"]}
    assert providers["TEST_BSE"]["status"] == "FRESH"
    assert providers["TEST_NSE"]["status"] == "STALE"


@pytest.mark.asyncio
async def test_analytics_queries_leave_database_unchanged(tmp_path):
    path = tmp_path / "beta.db"
    await _seed_database(path)
    before = await _table_counts(path)

    service = AnalyticsService(str(path), backtests=(_backtest_run(),))
    await service.snapshot(now=NOW)

    assert await _table_counts(path) == before
    assert service.access_mode == "READ_ONLY"
    assert not any(hasattr(service, name) for name in ("insert", "update", "delete", "execute"))


async def _table_counts(path) -> dict[str, int]:
    conn = await open_beta_database(str(path), read_only=True)
    try:
        counts = {}
        for table in (
            "beta_signals", "beta_scanner_decisions", "beta_scanner_rankings", "beta_quotes"
        ):
            async with conn.execute(f"SELECT COUNT(*) FROM {table}") as cursor:
                counts[table] = int((await cursor.fetchone())[0])
        return counts
    finally:
        await conn.close()
