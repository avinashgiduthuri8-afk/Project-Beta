"""
PROJECT-ALPHA — Phase 2 Comprehensive Paper Close & Accounting Tests.

Validates:
1. STOP_LOSS & TAKE_PROFIT close pipeline creates exactly ONE trade.
2. Realized P&L calculation and statutory 1.572% friction deduction.
3. Quote currency preservation and FX normalization (USDT/USD vs INR).
4. Duplicate close protection (sequential and concurrent).
5. Trade uniqueness in TradeRepository.
6. /pnl reconciliation (realized, unrealized, total).
7. Service restart safety and P&L persistence.
8. PaperExecutionRouter persistence failure handling.
9. Scanner signal replay idempotency.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
import pytest

from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.config import AppConfig
from core.repository.db import Database
from core.repository.position_repo import PositionRepository
from core.repository.trade_repo import TradeRepository
from core.types import BotMode, BotName, ExitReason, Position, PositionStatus, Trade
from execution.auto_trader import AutoTradeRouter
from execution.position_manager import PositionManager, STATUTORY_ROUND_TRIP_DRAG_RATE
from background.portfolio.aggregator import PortfolioAggregator
from background.portfolio.service import PortfolioService
from paper.execution.router import PaperExecutionRouter


@pytest.fixture
async def setup_env(tmp_path):
    db_file = str(tmp_path / f"test_paper_{uuid.uuid4().hex[:6]}.db")
    db = Database(db_file)
    await db.open()

    conn = db.connection
    pos_repo = PositionRepository(conn)
    trade_repo = TradeRepository(conn)
    bus = EventBus()
    pos_manager = PositionManager(position_repo=pos_repo, trade_repo=trade_repo, bus=bus)

    cfg = AppConfig()
    cfg.deployment_mode = "PAPER"
    cfg.usdt_inr_rate = 91.50

    yield {
        "db": db,
        "pos_repo": pos_repo,
        "trade_repo": trade_repo,
        "bus": bus,
        "pos_manager": pos_manager,
        "config": cfg,
    }

    await db.close()


@pytest.mark.anyio
async def test_stop_loss_close_creates_single_trade(setup_env):
    pos_manager = setup_env["pos_manager"]
    trade_repo = setup_env["trade_repo"]

    pos = await pos_manager.open_position(
        bot=BotName.STE,
        coin="BTC",
        pair="BTC/INR",
        entry_price=5000000.0,
        qty=0.001,  # 5,000 INR
        stop_loss=4800000.0,
        take_profit=5300000.0,
        mode=BotMode.PAPER,
    )

    closed_pos, trade = await pos_manager.close_position(
        position_id=pos.id,
        exit_price=4800000.0,
        exit_reason=ExitReason.STOP_LOSS,
    )

    assert closed_pos is not None
    assert trade is not None
    assert closed_pos.status == PositionStatus.CLOSED
    assert trade.position_id == pos.id
    assert trade.exit_reason == ExitReason.STOP_LOSS
    assert trade.exit_price == 4800000.0
    assert trade.bot == BotName.STE
    assert trade.pair == "BTC/INR"

    # Exactly 1 trade in DB
    trades = await trade_repo.get_by_position(pos.id)
    assert len(trades) == 1
    assert trades[0].id == trade.id


@pytest.mark.anyio
async def test_take_profit_close_creates_single_trade(setup_env):
    pos_manager = setup_env["pos_manager"]
    trade_repo = setup_env["trade_repo"]

    pos = await pos_manager.open_position(
        bot=BotName.HDA,
        coin="ETH",
        pair="ETH/INR",
        entry_price=200000.0,
        qty=0.01,  # 2,000 INR
        stop_loss=190000.0,
        take_profit=215000.0,
        mode=BotMode.PAPER,
    )

    closed_pos, trade = await pos_manager.close_position(
        position_id=pos.id,
        exit_price=215000.0,
        exit_reason=ExitReason.TAKE_PROFIT,
    )

    assert closed_pos.status == PositionStatus.CLOSED
    assert trade.exit_reason == ExitReason.TAKE_PROFIT
    assert trade.exit_price == 215000.0

    trades = await trade_repo.get_by_position(pos.id)
    assert len(trades) == 1
    assert trades[0].id == trade.id


@pytest.mark.anyio
async def test_realized_pnl_and_statutory_friction(setup_env):
    pos_manager = setup_env["pos_manager"]
    pos_repo = setup_env["pos_repo"]

    entry_px = 1000.0
    exit_px = 1100.0
    qty = 2.0  # Entry: 2000, Exit: 2200, Gross: +200

    pos = await pos_manager.open_position(
        bot=BotName.VCP,
        coin="SOL",
        pair="SOL/INR",
        entry_price=entry_px,
        qty=qty,
        mode=BotMode.PAPER,
    )

    closed_pos, trade = await pos_manager.close_position(
        position_id=pos.id,
        exit_price=exit_px,
        exit_reason=ExitReason.TAKE_PROFIT,
    )

    gross_pnl = (exit_px - entry_px) * qty  # 200.0
    total_traded_value = (entry_px * qty) + (exit_px * qty)  # 4200.0
    expected_friction = total_traded_value * (STATUTORY_ROUND_TRIP_DRAG_RATE / 2.0)  # 4200 * 0.00786 = 33.012
    expected_net = round(gross_pnl - expected_friction, 2)  # 166.99

    assert trade.pnl == expected_net
    assert trade.qty == qty
    assert trade.entry_price == entry_px
    assert trade.exit_price == exit_px

    # Verify DB position realized_pnl matches
    db_pos = await pos_repo.get_by_id(pos.id)
    assert db_pos.realized_pnl == expected_net


@pytest.mark.anyio
async def test_quote_currency_preservation_usdt(setup_env):
    pos_manager = setup_env["pos_manager"]

    # Open USDT pair
    pos = await pos_manager.open_position(
        bot=BotName.STE,
        coin="ADA",
        pair="ADA/USDT",
        entry_price=0.50,
        qty=500.0,  # 250 USDT
        mode=BotMode.PAPER,
    )

    closed_pos, trade = await pos_manager.close_position(
        position_id=pos.id,
        exit_price=0.55,
        exit_reason=ExitReason.TAKE_PROFIT,
    )

    # Gross = (0.55 - 0.50) * 500 = 25.0 USDT
    # Traded value = (0.50 * 500) + (0.55 * 500) = 250 + 275 = 525 USDT
    # Friction = 525 * 0.00786 = 4.1265 USDT
    # Net = 25 - 4.1265 = 20.87 USDT
    assert trade.pnl == 20.87
    assert trade.pair == "ADA/USDT"


@pytest.mark.anyio
async def test_portfolio_aggregator_inr_normalization():
    now = datetime.now(timezone.utc)

    # 1 INR position + 1 USDT position
    pos_inr = Position(
        id="pos-inr",
        bot=BotName.STE,
        coin="BTC",
        pair="BTC/INR",
        qty=0.001,
        entry_price=5000000.0,
        current_price=5100000.0,
        unrealised_pnl=100.0,  # +100 INR
        entry_time=now,
        mode=BotMode.PAPER,
        status=PositionStatus.OPEN,
    )

    pos_usdt = Position(
        id="pos-usdt",
        bot=BotName.HDA,
        coin="ETH",
        pair="ETH/USDT",
        qty=0.1,
        entry_price=2000.0,
        current_price=2100.0,
        unrealised_pnl=10.0,  # +10 USDT -> 915.0 INR
        entry_time=now,
        mode=BotMode.PAPER,
        status=PositionStatus.OPEN,
    )

    trade_inr = Trade(
        id="trade-inr",
        position_id="pos-old-inr",
        bot=BotName.STE,
        coin="BTC",
        pair="BTC/INR",
        entry_price=5000000.0,
        exit_price=5200000.0,
        qty=0.001,
        pnl=150.0,  # +150 INR
        pnl_pct=3.0,
        entry_time=now,
        exit_time=now,
        exit_reason=ExitReason.TAKE_PROFIT,
        mode=BotMode.PAPER,
    )

    trade_usdt = Trade(
        id="trade-usdt",
        position_id="pos-old-usdt",
        bot=BotName.HDA,
        coin="SOL",
        pair="SOL/USDT",
        entry_price=100.0,
        exit_price=110.0,
        qty=1.0,
        pnl=8.0,  # +8 USDT -> 8 * 91.50 = 732.0 INR
        pnl_pct=8.0,
        entry_time=now,
        exit_time=now,
        exit_reason=ExitReason.TAKE_PROFIT,
        mode=BotMode.PAPER,
    )

    snapshot = PortfolioAggregator.aggregate(
        positions=[pos_inr, pos_usdt],
        closed_trades=[trade_inr, trade_usdt],
        base_cash=100000.0,
        fx_rate=91.50,
    )

    # Realized: 150 (INR) + (8 * 91.50 = 732) = 882.00 INR
    assert snapshot.total_realised_pnl == 882.00

    # Unrealized: 100 (INR) + (10 * 91.50 = 915) = 1015.00 INR
    assert snapshot.total_unrealised_pnl == 1015.00

    # Deployed: (0.001 * 5000000 = 5000 INR) + (0.1 * 2000 * 91.50 = 18300 INR) = 23300.00 INR
    assert snapshot.total_deployed == 23300.00


@pytest.mark.anyio
async def test_duplicate_close_protection(setup_env):
    pos_manager = setup_env["pos_manager"]
    trade_repo = setup_env["trade_repo"]

    pos = await pos_manager.open_position(
        bot=BotName.STE,
        coin="BTC",
        pair="BTC/INR",
        entry_price=100000.0,
        qty=0.01,
        mode=BotMode.PAPER,
    )

    # First close
    c1, t1 = await pos_manager.close_position(
        position_id=pos.id,
        exit_price=105000.0,
        exit_reason=ExitReason.TAKE_PROFIT,
    )
    assert c1 is not None and t1 is not None
    initial_pnl = t1.pnl

    # Second close attempt on already closed position
    c2, t2 = await pos_manager.close_position(
        position_id=pos.id,
        exit_price=105000.0,
        exit_reason=ExitReason.TAKE_PROFIT,
    )

    # Must return safely without creating duplicate trade
    assert c2.id == pos.id
    assert c2.status == PositionStatus.CLOSED
    assert t2.id == t1.id
    assert t2.pnl == initial_pnl

    # Verify only 1 trade in DB
    all_trades = await trade_repo.get_by_position(pos.id)
    assert len(all_trades) == 1
    assert all_trades[0].id == t1.id


@pytest.mark.anyio
async def test_concurrent_close_protection(setup_env):
    pos_manager = setup_env["pos_manager"]
    trade_repo = setup_env["trade_repo"]

    pos = await pos_manager.open_position(
        bot=BotName.VCP,
        coin="MATIC",
        pair="MATIC/INR",
        entry_price=50.0,
        qty=10.0,
        mode=BotMode.PAPER,
    )

    # Run two closes simultaneously
    res1, res2 = await asyncio.gather(
        pos_manager.close_position(pos.id, 55.0, ExitReason.TAKE_PROFIT),
        pos_manager.close_position(pos.id, 55.0, ExitReason.TAKE_PROFIT),
    )

    # Exactly 1 trade must exist
    all_trades = await trade_repo.get_by_position(pos.id)
    assert len(all_trades) == 1


@pytest.mark.anyio
async def test_trade_repo_idempotency_insert(setup_env):
    trade_repo = setup_env["trade_repo"]
    pos_repo = setup_env["pos_repo"]
    now = datetime.now(timezone.utc)

    # Insert parent position to satisfy foreign key constraint
    dummy_pos = Position(
        id="pos-shared",
        bot=BotName.STE,
        coin="BTC",
        pair="BTC/INR",
        entry_price=100.0,
        qty=1.0,
        entry_time=now,
        mode=BotMode.PAPER,
    )
    await pos_repo.insert(dummy_pos)

    t1 = Trade(
        id="t-1",
        position_id="pos-shared",
        bot=BotName.STE,
        coin="BTC",
        pair="BTC/INR",
        entry_price=100.0,
        exit_price=110.0,
        qty=1.0,
        pnl=8.0,
        pnl_pct=8.0,
        entry_time=now,
        exit_time=now,
        exit_reason=ExitReason.TAKE_PROFIT,
        mode=BotMode.PAPER,
    )

    t2 = Trade(
        id="t-2",  # Different ID, same position_id
        position_id="pos-shared",
        bot=BotName.STE,
        coin="BTC",
        pair="BTC/INR",
        entry_price=100.0,
        exit_price=110.0,
        qty=1.0,
        pnl=8.0,
        pnl_pct=8.0,
        entry_time=now,
        exit_time=now,
        exit_reason=ExitReason.TAKE_PROFIT,
        mode=BotMode.PAPER,
    )

    id1 = await trade_repo.insert(t1)
    id2 = await trade_repo.insert(t2)

    assert id1 == "t-1"
    assert id2 == "t-1"  # Returns existing trade ID, skips inserting duplicate

    trades = await trade_repo.get_by_position("pos-shared")
    assert len(trades) == 1
    assert trades[0].id == "t-1"


@pytest.mark.anyio
async def test_pnl_reconciliation_and_restart_safety(setup_env):
    pos_repo = setup_env["pos_repo"]
    trade_repo = setup_env["trade_repo"]
    pos_manager = setup_env["pos_manager"]
    bus = setup_env["bus"]
    cfg = setup_env["config"]

    portfolio_svc = PortfolioService(
        bus=bus,
        position_repo=pos_repo,
        trade_repo=trade_repo,
        config=cfg,
    )

    # 1. Open 2 positions
    p1 = await pos_manager.open_position(
        bot=BotName.STE, coin="BTC", pair="BTC/INR", entry_price=100000.0, qty=0.01, mode=BotMode.PAPER
    )
    p2 = await pos_manager.open_position(
        bot=BotName.HDA, coin="ETH", pair="ETH/USDT", entry_price=2000.0, qty=0.1, mode=BotMode.PAPER
    )

    # 2. Close 1 position
    _, t1 = await pos_manager.close_position(p1.id, 105000.0, ExitReason.TAKE_PROFIT)

    # 3. Mark price on remaining open position
    await pos_manager.update_mark_price(p2, 2100.0)

    # 4. Generate snapshot
    snap1 = await portfolio_svc.get_snapshot()

    # Verify reconciliation formula
    # Realized PnL == sum(trades.pnl * fx)
    all_trades = await trade_repo.get_all(mode=BotMode.PAPER)
    expected_realized = sum(
        t.pnl * (91.50 if t.pair.endswith("USDT") else 1.0) for t in all_trades
    )
    assert snap1.total_realised_pnl == round(expected_realized, 2)

    # Unrealized PnL == sum(open_positions.unrealised_pnl * fx)
    open_pos = await pos_repo.get_open(mode=BotMode.PAPER)
    expected_unrealized = sum(
        pos.unrealised_pnl * (91.50 if pos.pair.endswith("USDT") else 1.0) for pos in open_pos
    )
    assert snap1.total_unrealised_pnl == round(expected_unrealized, 2)

    # 5. Simulate SERVICE RESTART (new instance of PortfolioService)
    restarted_portfolio_svc = PortfolioService(
        bus=bus,
        position_repo=pos_repo,
        trade_repo=trade_repo,
        config=cfg,
    )
    snap2 = await restarted_portfolio_svc.get_snapshot()

    # /pnl must remain 100% identical after restart
    assert snap2.total_realised_pnl == snap1.total_realised_pnl
    assert snap2.total_unrealised_pnl == snap1.total_unrealised_pnl
    assert snap2.total_aum == snap1.total_aum


@pytest.mark.anyio
async def test_paper_execution_router_persistence_failure(setup_env):
    pos_repo = setup_env["pos_repo"]
    trade_repo = setup_env["trade_repo"]
    bus = setup_env["bus"]

    auto_trader = AutoTradeRouter(bus=bus, dry_run=True, position_repo=pos_repo)

    # Case 1: position_manager is None
    router_no_pm = PaperExecutionRouter(bus=bus, auto_trader=auto_trader, position_manager=None)
    payload = {
        "id": "sig-test-1",
        "signal_id": "sig-test-1",
        "mode": "PAPER",
        "coin": "BTC",
        "pair": "BTC/INR",
        "price": 5000000.0,
        "score": 85.0,
    }
    res = await router_no_pm.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
    assert res["success"] is False
    assert res["error"] == "POSITION_MANAGER_UNAVAILABLE"

    # Case 2: position_manager raises an exception during open_position
    class FailingPositionManager(PositionManager):
        async def open_position(self, *args, **kwargs):
            raise RuntimeError("Database write disk error")

    failing_pm = FailingPositionManager(position_repo=pos_repo, trade_repo=trade_repo)
    router_failing = PaperExecutionRouter(bus=bus, auto_trader=auto_trader, position_manager=failing_pm)

    payload2 = {
        "id": "sig-test-2",
        "signal_id": "sig-test-2",
        "mode": "PAPER",
        "coin": "ETH",
        "pair": "ETH/INR",
        "price": 200000.0,
        "score": 88.0,
    }
    res2 = await router_failing.handle_signal_event(EventType.SIGNAL_GENERATED, payload2)
    assert res2["success"] is False
    assert "POSITION_PERSISTENCE_FAILED" in res2["error"]

    # Verify DB has 0 positions inserted
    open_pos = await pos_repo.get_open()
    assert len(open_pos) == 0


@pytest.mark.anyio
async def test_scanner_signal_replay_idempotency(setup_env):
    pos_repo = setup_env["pos_repo"]
    trade_repo = setup_env["trade_repo"]
    bus = setup_env["bus"]
    pos_manager = setup_env["pos_manager"]

    auto_trader = AutoTradeRouter(bus=bus, dry_run=True, position_repo=pos_repo)
    router = PaperExecutionRouter(bus=bus, auto_trader=auto_trader, position_manager=pos_manager)

    sig_id = "SIG-UNIQUE-101"
    payload = {
        "id": sig_id,
        "signal_id": sig_id,
        "mode": "PAPER",
        "coin": "SOL",
        "pair": "SOL/INR",
        "price": 10000.0,
        "score": 92.0,
    }

    # Initial intake
    r1 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
    assert r1["success"] is True
    pos_id = r1["position_id"]

    # Replay intake (e.g. scanner polling same signal again or after restart)
    r2 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
    assert r2["success"] is False
    assert r2["error"] in ("DUPLICATE_SIGNAL", "SIGNAL_ALREADY_EXECUTED")

    # Verify DB still has exactly 1 position for this signal
    existing = await pos_repo.get_by_signal_id(sig_id)
    assert existing is not None
    assert existing.id == pos_id

    all_positions = await pos_repo.get_open()
    assert len(all_positions) == 1
