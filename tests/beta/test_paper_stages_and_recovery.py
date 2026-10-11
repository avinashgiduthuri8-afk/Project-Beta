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
