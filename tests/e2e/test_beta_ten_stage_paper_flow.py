"""Offline proof of Beta's complete Scanner -> Paper -> Learner workflow."""

from datetime import datetime, timezone
from decimal import Decimal

import aiosqlite
import pytest

from core.repository.beta_store import BetaStore
from core.repository.paper_execution_repo import PaperExecutionRepository
from core.repository.quote_repo import QuoteRepository
from learner.indian_service import IndianLearnerService
from market.models import MarketQuote
from market.symbols import StockSymbol
from paper.indian_service import IndianPaperService
from scanner.indian_service import IndianScannerService


class OfflineProvider:
    def __init__(self, symbol, now):
        self.quote = MarketQuote(symbol, Decimal("100"), now, "FAKE_YAHOO", 0)
        self.candles = [
            {"timestamp": int(now.timestamp() * 1000) + i * 60_000, "open": 80 + i,
             "high": 82 + i, "low": 79 + i, "close": 81 + i, "volume": 1_000_000}
            for i in range(20)
        ]

    async def get_quote(self, symbol):
        return self.quote

    async def get_candles(self, symbol, timeframe, limit):
        return self.candles[-limit:]

    async def close(self):
        pass


async def memory_db():
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    store = BetaStore(conn)
    await store.initialize()
    return conn, store


async def count(conn, table):
    async with conn.execute(f"SELECT COUNT(*) FROM {table}") as cursor:
        return (await cursor.fetchone())[0]


@pytest.mark.asyncio
async def test_offline_ten_stage_flow_and_restart_idempotency():
    now = datetime(2026, 10, 9, 5, 0, tzinfo=timezone.utc)  # Friday 10:30 IST
    symbol = StockSymbol.parse("NSE:RELIANCE")
    scanner_conn, scanner_store = await memory_db()
    paper_conn, _ = await memory_db()
    learner_conn, _ = await memory_db()
    provider = OfflineProvider(symbol, now)

    scanner = IndianScannerService(
        provider, scanner_store, [symbol], clock=lambda: now,
        quote_repo=QuoteRepository(scanner_conn),
    )
    scan_report = await scanner.poll()
    signal = await scanner_store.get_signal(scan_report["signal_id"])
    quote = await QuoteRepository(scanner_conn).get_latest(symbol.canonical)

    paper_repo = PaperExecutionRepository(paper_conn)
    await paper_repo.initialize_account(Decimal("10000"))
    paper = IndianPaperService(paper_repo, Decimal("1000"))
    paper_report = await paper.handle_signal({**signal, "mode": "PAPER"}, quote, now)
    close = await paper_repo.close_position(
        paper_report["position_id"], Decimal("110"), signal["strategy"], now
    )

    learner = IndianLearnerService(paper_conn, min_trades=1, write_conn=learner_conn)
    learn_report = await learner.run_cycle()

    assert scan_report["stages"] + paper_report["stages"] + learn_report["stages"] == list(range(1, 11))
    assert paper_report["quantity"] == int(paper_report["quantity"])
    assert Decimal(close["net_pnl"]) < (Decimal("110") - Decimal("100")) * paper_report["quantity"]
    assert close["exit_charges"] > 0
    assert await count(scanner_conn, "beta_signals") == 1
    assert await count(scanner_conn, "beta_positions") == 0
    assert await count(paper_conn, "beta_positions") == 1
    assert await count(paper_conn, "beta_learning_audit") == 0
    assert await count(learner_conn, "beta_learning_audit") == 1
    assert await count(learner_conn, "beta_journal") == 0

    # New service objects simulate a complete restart against durable stores.
    scan_again = await IndianScannerService(provider, scanner_store, [symbol], clock=lambda: now).poll()
    paper_again = await IndianPaperService(PaperExecutionRepository(paper_conn), Decimal("1000")).handle_signal({**signal, "mode": "PAPER"}, quote, now)
    learn_again = await IndianLearnerService(paper_conn, 1, write_conn=learner_conn).run_cycle()
    assert scan_again["duplicates"] == 1
    assert paper_again["reason"] == "SIGNAL_ALREADY_CONSUMED"
    assert learn_again["new_audits"] == 0

    await scanner_conn.close()
    await paper_conn.close()
    await learner_conn.close()
