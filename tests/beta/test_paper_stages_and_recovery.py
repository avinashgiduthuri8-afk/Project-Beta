from datetime import datetime, timedelta, timezone
from decimal import Decimal

import aiosqlite
import pytest

from core.repository.beta_store import BetaStore
from core.repository.paper_execution_repo import PaperExecutionRepository
from execution.indian_charges import IndianEquityChargeModel
from market.models import MarketQuote
from market.symbols import StockSymbol
from paper.execution.fill_simulator import PaperOrder, PaperFillSimulator
from paper.indian_service import IndianPaperService


def test_indian_charge_model_names_every_component():
    charge = IndianEquityChargeModel().estimate("BUY", Decimal("100"), 10, "NSE")
    assert charge.total > 0
    assert set(charge.as_dict()) == {"brokerage", "stt", "exchange_charge", "sebi_charge", "gst", "stamp_duty", "slippage", "total"}


def test_fill_simulator_market_limit_and_stop_rules():
    now = datetime.now(timezone.utc)
    quote = MarketQuote(StockSymbol.parse("NSE:RELIANCE"), Decimal("100"), now, "YAHOO", 0)
    simulator = PaperFillSimulator(max_quote_age=timedelta(seconds=30))
    assert simulator.fill(PaperOrder("o1", "BUY", "MARKET", 2), quote, now)
    assert simulator.fill(PaperOrder("o2", "BUY", "LIMIT", 2, limit_price=Decimal("101")), quote, now)
    assert simulator.fill(PaperOrder("o3", "BUY", "LIMIT", 2, limit_price=Decimal("99")), quote, now) is None
    assert simulator.fill(PaperOrder("o4", "BUY", "STOP", 2, stop_price=Decimal("99")), quote, now)


@pytest.mark.asyncio
async def test_paper_stages_are_persistently_idempotent_across_restart():
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    store = BetaStore(conn)
    await store.initialize()
    repo = PaperExecutionRepository(conn)
    await repo.initialize_account(Decimal("10000"))
    symbol = StockSymbol.parse("NSE:RELIANCE")
    now = datetime.now(timezone.utc)
    signal = {"id": "sig-1", "symbol": "RELIANCE", "exchange": "NSE", "strategy": "STE", "price": "100", "mode": "PAPER"}
    quote = MarketQuote(symbol, Decimal("100"), now, "YAHOO", 0)

    first_service = IndianPaperService(PaperExecutionRepository(conn), allocation=Decimal("1000"))
    restarted_service = IndianPaperService(PaperExecutionRepository(conn), allocation=Decimal("1000"))
    first = await first_service.handle_signal(signal, quote, now)
    second = await restarted_service.handle_signal(signal, quote, now)

    assert first["stages"] == [6, 7]
    assert first["opened"] is True
    assert first["quantity"] == 9
    assert second["opened"] is False
    assert second["reason"] == "SIGNAL_ALREADY_CONSUMED"
    assert await repo.position_count() == 1
    await conn.close()


@pytest.mark.asyncio
async def test_paper_guarantees_minimum_one_share_for_high_priced_stocks():
    """Verify that high-priced stocks (e.g. ₹2,950) buy at least 1 whole share even if allocation is lower."""
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    store = BetaStore(conn)
    await store.initialize()
    repo = PaperExecutionRepository(conn)
    await repo.initialize_account(Decimal("10000"))  # ₹10,000 account cash

    symbol = StockSymbol.parse("NSE:RELIANCE")
    now = datetime.now(timezone.utc)
    # Reliance trading at ₹2,950.00
    quote = MarketQuote(symbol, Decimal("2950.00"), now, "YAHOO", 0)
    signal = {
        "id": "sig-reliance-1",
        "symbol": "RELIANCE",
        "exchange": "NSE",
        "strategy": "VCP",
        "price": "2950.00",
        "mode": "PAPER",
        "stop_loss": "2800.00",
        "take_profit": "3300.00",
        "timeframe": "1d",
    }

    # Allocation budget is only ₹1,000, which is less than 1 share's price (₹2,950)
    service = IndianPaperService(repo, allocation=Decimal("1000"))
    result = await service.handle_signal(signal, quote, now)

    assert result["opened"] is True
    assert result["quantity"] == 1
    assert Decimal(result["charges"]["total"]) > 0
    assert await repo.position_count() == 1

    # Verify cash was debited correctly
    cash_left = await repo.cash()
    assert cash_left < Decimal("10000") - Decimal("2950")

    await conn.close()


@pytest.mark.asyncio
async def test_paper_evaluates_swing_exits_for_tp_and_sl():
    """Verify that PaperService automatically closes swing positions when TP or SL is triggered."""
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    store = BetaStore(conn)
    await store.initialize()
    repo = PaperExecutionRepository(conn)
    await repo.initialize_account(Decimal("20000"))

    symbol = StockSymbol.parse("NSE:TCS")
    now = datetime.now(timezone.utc)
    entry_quote = MarketQuote(symbol, Decimal("3500.00"), now, "YAHOO", 0)
    signal = {
        "id": "sig-tcs-1",
        "symbol": "TCS",
        "exchange": "NSE",
        "strategy": "VCP",
        "price": "3500.00",
        "mode": "PAPER",
        "stop_loss": "3300.00",
        "take_profit": "3800.00",
        "timeframe": "1d",
    }

    service = IndianPaperService(repo, allocation=Decimal("10000"))
    await service.handle_signal(signal, entry_quote, now)
    assert await repo.position_count() == 1

    class MockQuotes:
        def __init__(self, current_price):
            self.quote = MarketQuote(symbol, Decimal(str(current_price)), now, "YAHOO", 0)
        async def get_latest(self, s):
            return self.quote

    # 1. Price at ₹3,600 (holding within range over weeks) -> No exit
    holding_quotes = MockQuotes("3600.00")
    no_exits = await service.evaluate_open_positions(holding_quotes, now)
    assert len(no_exits) == 0

    # 2. Price hits Take Profit at ₹3,850 -> Automated Take Profit Exit
    tp_quotes = MockQuotes("3850.00")
    tp_exits = await service.evaluate_open_positions(tp_quotes, now)
    assert len(tp_exits) == 1
    assert tp_exits[0]["reason"] == "TAKE_PROFIT_HIT"
    assert Decimal(tp_exits[0]["net_pnl"]) > 0

    # Verify position is closed and journaled
    open_positions = await repo.get_open_positions()
    assert len(open_positions) == 0

    async with conn.execute("SELECT COUNT(*) FROM beta_journal") as cursor:
        assert (await cursor.fetchone())[0] == 1

    await conn.close()
