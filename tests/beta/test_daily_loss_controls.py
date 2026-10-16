from datetime import date, datetime, timezone
from decimal import Decimal

import aiosqlite
import pytest

from core.repository.beta_store import BetaStore
from core.repository.paper_execution_repo import PaperExecutionRepository
from market.models import MarketQuote
from market.symbols import StockSymbol
from paper.indian_service import IndianPaperService
from services.paper_bot.risk import DailyRiskState, PaperRiskPolicy


def state(loss: str, consecutive_losses: int = 0, status: str = "ACTIVE") -> DailyRiskState:
    return DailyRiskState(date(2026, 10, 16), -Decimal(loss), Decimal("0"), Decimal("0"), consecutive_losses, status)


@pytest.mark.parametrize(
    ("loss", "allowed", "code"),
    [("749.99", True, "ACTIVE"), ("750", True, "DAILY_LOSS_WARNING"),
     ("900", False, "NEW_ENTRIES_BLOCKED"), ("1000", False, "DAILY_LOSS_LIMIT_REACHED")],
)
def test_daily_loss_thresholds(loss, allowed, code):
    decision = PaperRiskPolicy().evaluate_daily_state(state(loss))
    assert decision.allowed is allowed
    assert decision.code == code


@pytest.mark.asyncio
async def test_persisted_emergency_stop_survives_restart():
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    store = BetaStore(conn)
    await store.initialize()
    repo = PaperExecutionRepository(conn)
    await repo.initialize_account(Decimal("10000"))
    await repo.save_daily_risk_state(state("1000", status="EMERGENCY_STOP"), datetime(2026, 10, 16, 5, tzinfo=timezone.utc))

    reloaded = await PaperExecutionRepository(conn).get_daily_risk_state(date(2026, 10, 16), {})

    assert reloaded.state == "EMERGENCY_STOP"
    assert PaperRiskPolicy().evaluate_daily_state(reloaded).code == "DAILY_LOSS_LIMIT_REACHED"
    await conn.close()


@pytest.mark.asyncio
async def test_entry_is_blocked_by_persisted_daily_state():
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    store = BetaStore(conn)
    await store.initialize()
    repo = PaperExecutionRepository(conn)
    await repo.initialize_account(Decimal("10000"))
    now = datetime(2026, 10, 16, 5, tzinfo=timezone.utc)
    await repo.save_daily_risk_state(state("900", status="ENTRY_BLOCKED"), now)
    symbol = StockSymbol.parse("NSE:TEST")
    quote = MarketQuote(symbol, Decimal("100"), now, "TEST")
    signal = {"id": "sig-risk", "symbol": "TEST", "exchange": "NSE", "mode": "PAPER", "sector": "METALS"}

    result = await IndianPaperService(repo, Decimal("1000")).handle_signal(signal, quote, now)

    assert result["opened"] is False
    assert result["reason"] == "NEW_ENTRIES_BLOCKED"
    assert await repo.position_count() == 0
    await conn.close()


@pytest.mark.asyncio
async def test_emergency_stop_flattens_open_paper_positions():
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    store = BetaStore(conn)
    await store.initialize()
    repo = PaperExecutionRepository(conn)
    await repo.initialize_account(Decimal("10000"))
    now = datetime(2026, 10, 16, 5, tzinfo=timezone.utc)
    await repo.open_position_once(
        "sig-open", {"id": "pos-open", "symbol": "TEST", "exchange": "NSE",
        "quantity": 5, "entry_price": Decimal("100"), "charges": Decimal("1"),
        "charge_breakdown": "{}", "opened_at": now.isoformat()}, Decimal("501"),
    )
    await repo.save_daily_risk_state(state("1000", status="EMERGENCY_STOP"), now)
    symbol = StockSymbol.parse("NSE:TEST")
    quote = MarketQuote(symbol, Decimal("90"), now, "TEST")

    class Quotes:
        async def get_latest(self, canonical):
            return quote

    report = await IndianPaperService(repo, Decimal("1000")).enforce_daily_limits(Quotes(), now)

    assert report["state"] == "EMERGENCY_STOP"
    assert report["closed"] == 1
    assert await repo.get_open_positions() == []
    await conn.close()
