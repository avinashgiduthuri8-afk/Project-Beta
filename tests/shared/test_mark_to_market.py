"""
Focused Regression Tests for PROJECT-ALPHA Mark-to-Market Fix.

Verifies:
- Test A: current_price updates from entry_price on fresh ticker
- Test B: Positive unrealized P&L calculation: (105 - 100) * 2 = +10.0
- Test C: Negative unrealized P&L calculation: (95 - 100) * 2 = -10.0
- Test D: Multiple positions receive independent ticker prices
- Test E: Trailing stop ratchets upward with peak price and triggers exit on pullback
- Test F: Missing / stale / 0 ticker preserves last known mark (no overwrite)
- Test G: Closed positions are not updated by mark updates
- Test H: Polling loop continues gracefully even if one position lacks ticker
- Test I: PositionRepository.get_open() returns updated current_price and unrealised_pnl
- Test J: PortfolioService.get_snapshot() aggregates updated total_unrealised_pnl
- Test K/L/M: Core immutability: entry_price, qty, and TP/SL remain unchanged
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from background.portfolio.service import PortfolioService
from core.bus.event_bus import EventBus
from core.config import AppConfig
from core.repository.db import Database
from core.repository.event_log_repo import EventLogRepository
from core.repository.position_repo import PositionRepository
from core.repository.trade_repo import TradeRepository
from core.types import (
    BotMode,
    BotName,
    ExitReason,
    Position,
    PositionStatus,
)
from execution.service import TradingService
from execution.trading.subaccount_manager import CoinDCXExecutionManager


@pytest.fixture
async def setup_env(tmp_path):
    """Initializes isolated SQLite database and services for testing."""
    db_file = str(tmp_path / f"test_m2m_{uuid.uuid4().hex[:6]}.db")
    db = Database(db_file)
    await db.open()

    conn = db.connection
    pos_repo = PositionRepository(conn)
    trade_repo = TradeRepository(conn)
    event_repo = EventLogRepository(conn)
    bus = EventBus()
    cfg = AppConfig(v2_trading_enabled=True)
    exec_mgr = CoinDCXExecutionManager()

    trading_svc = TradingService(
        bus=bus,
        position_repo=pos_repo,
        trade_repo=trade_repo,
        event_log_repo=event_repo,
        config=cfg,
        subaccount_manager=exec_mgr,
    )
    await trading_svc.start()

    portfolio_svc = PortfolioService(
        bus=bus,
        position_repo=pos_repo,
        trade_repo=trade_repo,
        config=cfg,
    )
    await portfolio_svc.start()

    yield {
        "db": db,
        "pos_repo": pos_repo,
        "trade_repo": trade_repo,
        "event_repo": event_repo,
        "bus": bus,
        "trading_svc": trading_svc,
        "portfolio_svc": portfolio_svc,
    }

    await trading_svc.stop()
    await portfolio_svc.stop()
    await db.close()


# ── Test A: current_price updates from entry_price on fresh ticker ───────────


@pytest.mark.anyio
async def test_a_current_price_updates_on_fresh_ticker(setup_env):
    env = setup_env
    pos_repo: PositionRepository = env["pos_repo"]
    trading_svc: TradingService = env["trading_svc"]

    now = datetime.now(timezone.utc)
    pos = Position(
        id="pos-btc-01",
        bot=BotName.STE,
        coin="BTC",
        pair="BTC/INR",
        qty=0.001,
        entry_price=5000000.0,
        entry_time=now,
        mode=BotMode.PAPER,
        status=PositionStatus.OPEN,
        current_price=5000000.0,
        stop_loss=4800000.0,
        take_profit=5500000.0,
    )
    await pos_repo.insert(pos)

    # Simulate fresh ticker arrived with new price
    current_prices = {"BTC": 5100000.0, "BTC/INR": 5100000.0}
    await trading_svc.check_open_position_exits(current_prices)

    db_pos = await pos_repo.get_by_id("pos-btc-01")
    assert db_pos is not None
    assert db_pos.current_price == 5100000.0
    assert db_pos.current_price != db_pos.entry_price


# ── Test B: Positive unrealized P&L calculation ──────────────────────────────


@pytest.mark.anyio
async def test_b_positive_unrealized_pnl(setup_env):
    env = setup_env
    pos_repo: PositionRepository = env["pos_repo"]
    trading_svc: TradingService = env["trading_svc"]

    # Entry 100, Qty 2 -> (105 - 100) * 2 = +10.0
    now = datetime.now(timezone.utc)
    pos = Position(
        id="pos-eth-pos-pnl",
        bot=BotName.HDA,
        coin="ETH",
        pair="ETH/INR",
        qty=2.0,
        entry_price=100.0,
        entry_time=now,
        mode=BotMode.PAPER,
        status=PositionStatus.OPEN,
        current_price=100.0,
        stop_loss=90.0,
        take_profit=120.0,
    )
    await pos_repo.insert(pos)

    current_prices = {"ETH": 105.0}
    await trading_svc.check_open_position_exits(current_prices)

    db_pos = await pos_repo.get_by_id("pos-eth-pos-pnl")
    assert db_pos is not None
    assert db_pos.current_price == 105.0
    assert db_pos.unrealised_pnl == 10.0


# ── Test C: Negative unrealized P&L calculation ──────────────────────────────


@pytest.mark.anyio
async def test_c_negative_unrealized_pnl(setup_env):
    env = setup_env
    pos_repo: PositionRepository = env["pos_repo"]
    trading_svc: TradingService = env["trading_svc"]

    # Entry 100, Qty 2 -> (95 - 100) * 2 = -10.0
    now = datetime.now(timezone.utc)
    pos = Position(
        id="pos-sol-neg-pnl",
        bot=BotName.VCP,
        coin="SOL",
        pair="SOL/INR",
        qty=2.0,
        entry_price=100.0,
        entry_time=now,
        mode=BotMode.PAPER,
        status=PositionStatus.OPEN,
        current_price=100.0,
        stop_loss=80.0,
        take_profit=120.0,
    )
    await pos_repo.insert(pos)

    current_prices = {"SOL": 95.0}
    await trading_svc.check_open_position_exits(current_prices)

    db_pos = await pos_repo.get_by_id("pos-sol-neg-pnl")
    assert db_pos is not None
    assert db_pos.current_price == 95.0
    assert db_pos.unrealised_pnl == -10.0


# ── Test D: Multiple positions receive independent ticker prices ─────────────


@pytest.mark.anyio
async def test_d_multiple_positions_independent_prices(setup_env):
    env = setup_env
    pos_repo: PositionRepository = env["pos_repo"]
    trading_svc: TradingService = env["trading_svc"]

    now = datetime.now(timezone.utc)
    pos1 = Position(
        id="pos-multi-btc",
        bot=BotName.STE,
        coin="BTC",
        pair="BTC/INR",
        qty=0.01,
        entry_price=50000.0,
        entry_time=now,
        mode=BotMode.PAPER,
        status=PositionStatus.OPEN,
        current_price=50000.0,
        stop_loss=45000.0,
        take_profit=60000.0,
    )
    pos2 = Position(
        id="pos-multi-eth",
        bot=BotName.BBS,
        coin="ETH",
        pair="ETH/INR",
        qty=0.1,
        entry_price=3000.0,
        entry_time=now,
        mode=BotMode.PAPER,
        status=PositionStatus.OPEN,
        current_price=3000.0,
        stop_loss=2700.0,
        take_profit=3500.0,
    )
    await pos_repo.insert(pos1)
    await pos_repo.insert(pos2)

    current_prices = {
        "BTC": 52000.0,
        "ETH": 2900.0,
    }
    await trading_svc.check_open_position_exits(current_prices)

    db_pos1 = await pos_repo.get_by_id("pos-multi-btc")
    db_pos2 = await pos_repo.get_by_id("pos-multi-eth")

    assert db_pos1.current_price == 52000.0
    assert db_pos1.unrealised_pnl == round((52000.0 - 50000.0) * 0.01, 2)  # +20.0

    assert db_pos2.current_price == 2900.0
    assert db_pos2.unrealised_pnl == round((2900.0 - 3000.0) * 0.1, 2)  # -10.0


# ── Test E: Trailing stop ratchets upward with peak and triggers exit ────────


@pytest.mark.anyio
async def test_e_trailing_stop_ratchets_and_triggers_exit(setup_env):
    env = setup_env
    pos_repo: PositionRepository = env["pos_repo"]
    trading_svc: TradingService = env["trading_svc"]

    now = datetime.now(timezone.utc)
    # Entry 100, Stop Loss 90, Take Profit 150. Trailing pct = 3% (0.03)
    pos = Position(
        id="pos-trail-01",
        bot=BotName.STE,
        coin="ADA",
        pair="ADA/INR",
        qty=10.0,
        entry_price=100.0,
        entry_time=now,
        mode=BotMode.PAPER,
        status=PositionStatus.OPEN,
        current_price=100.0,
        stop_loss=90.0,
        take_profit=150.0,
    )
    await pos_repo.insert(pos)

    # Step 1: Price goes up to 110.0 -> peak=110, trailing_stop = 110 * 0.97 = 106.7
    await trading_svc.check_open_position_exits({"ADA": 110.0})
    db_pos = await pos_repo.get_by_id("pos-trail-01")
    assert db_pos.status == PositionStatus.OPEN
    assert db_pos.current_price == 110.0
    assert trading_svc.position_manager._peak_prices["pos-trail-01"] == 110.0
    assert (
        round(trading_svc.position_manager._trailing_stops["pos-trail-01"], 2) == 106.70
    )

    # Step 2: Price rises further to 120.0 -> peak=120, trailing_stop = 120 * 0.97 = 116.4
    await trading_svc.check_open_position_exits({"ADA": 120.0})
    db_pos = await pos_repo.get_by_id("pos-trail-01")
    assert db_pos.status == PositionStatus.OPEN
    assert db_pos.current_price == 120.0
    assert trading_svc.position_manager._peak_prices["pos-trail-01"] == 120.0
    assert (
        round(trading_svc.position_manager._trailing_stops["pos-trail-01"], 2) == 116.40
    )

    # Step 3: Minor pullback to 118.0 (above trailing stop 116.4) -> remains OPEN
    await trading_svc.check_open_position_exits({"ADA": 118.0})
    db_pos = await pos_repo.get_by_id("pos-trail-01")
    assert db_pos.status == PositionStatus.OPEN
    assert db_pos.current_price == 118.0
    # Peak must not drop
    assert trading_svc.position_manager._peak_prices["pos-trail-01"] == 120.0
    assert (
        round(trading_svc.position_manager._trailing_stops["pos-trail-01"], 2) == 116.40
    )

    # Step 4: Pullback drops to 115.0 (below trailing stop 116.4) -> triggers exit
    await trading_svc.check_open_position_exits({"ADA": 115.0})
    db_pos = await pos_repo.get_by_id("pos-trail-01")
    assert db_pos.status == PositionStatus.CLOSED
    assert db_pos.exit_price == 115.0
    assert db_pos.exit_reason == ExitReason.STOP_LOSS
    # Cleaned up from manager tracking dicts
    assert "pos-trail-01" not in trading_svc.position_manager._peak_prices
    assert "pos-trail-01" not in trading_svc.position_manager._trailing_stops


# ── Test F: Missing / stale / 0 ticker preserves last known mark ─────────────


@pytest.mark.anyio
async def test_f_missing_or_zero_ticker_preserves_last_mark(setup_env):
    env = setup_env
    pos_repo: PositionRepository = env["pos_repo"]
    trading_svc: TradingService = env["trading_svc"]

    now = datetime.now(timezone.utc)
    pos = Position(
        id="pos-dot-preserve",
        bot=BotName.HDA,
        coin="DOT",
        pair="DOT/INR",
        qty=5.0,
        entry_price=500.0,
        entry_time=now,
        mode=BotMode.PAPER,
        status=PositionStatus.OPEN,
        current_price=500.0,
        stop_loss=450.0,
        take_profit=600.0,
    )
    await pos_repo.insert(pos)

    # Initial good update: price 520.0
    await trading_svc.check_open_position_exits({"DOT": 520.0})
    db_pos = await pos_repo.get_by_id("pos-dot-preserve")
    assert db_pos.current_price == 520.0
    assert db_pos.unrealised_pnl == 100.0  # (520 - 500) * 5

    # Ticker missing entirely
    await trading_svc.check_open_position_exits({})
    db_pos_after_missing = await pos_repo.get_by_id("pos-dot-preserve")
    assert db_pos_after_missing.current_price == 520.0
    assert db_pos_after_missing.unrealised_pnl == 100.0

    # Ticker is 0.0 or negative
    await trading_svc.check_open_position_exits({"DOT": 0.0})
    db_pos_after_zero = await pos_repo.get_by_id("pos-dot-preserve")
    assert db_pos_after_zero.current_price == 520.0
    assert db_pos_after_zero.unrealised_pnl == 100.0


# ── Test G: Closed positions are not updated ─────────────────────────────────


@pytest.mark.anyio
async def test_g_closed_positions_not_updated(setup_env):
    env = setup_env
    pos_repo: PositionRepository = env["pos_repo"]
    trading_svc: TradingService = env["trading_svc"]

    now = datetime.now(timezone.utc)
    pos = Position(
        id="pos-closed-01",
        bot=BotName.STE,
        coin="XRP",
        pair="XRP/INR",
        qty=100.0,
        entry_price=50.0,
        entry_time=now,
        mode=BotMode.PAPER,
        status=PositionStatus.OPEN,
        current_price=50.0,
        stop_loss=45.0,
        take_profit=60.0,
    )
    await pos_repo.insert(pos)
    await pos_repo.close(
        "pos-closed-01", exit_price=55.0, exit_reason=ExitReason.TAKE_PROFIT
    )

    # Verify initially closed
    closed_pos = await pos_repo.get_by_id("pos-closed-01")
    assert closed_pos.status == PositionStatus.CLOSED
    assert closed_pos.exit_price == 55.0

    # Poll with higher price (70.0) — closed position should not be touched
    await trading_svc.check_open_position_exits({"XRP": 70.0})
    db_pos = await pos_repo.get_by_id("pos-closed-01")
    assert db_pos.status == PositionStatus.CLOSED
    assert db_pos.exit_price == 55.0


# ── Test H: Polling loop continues even if one coin lacks ticker ─────────────


@pytest.mark.anyio
async def test_h_loop_continues_when_coin_lacks_ticker(setup_env):
    env = setup_env
    pos_repo: PositionRepository = env["pos_repo"]
    trading_svc: TradingService = env["trading_svc"]

    now = datetime.now(timezone.utc)
    pos_missing = Position(
        id="pos-missing-coin",
        bot=BotName.STE,
        coin="NO_TICKER_COIN",
        pair="NO_TICKER_COIN/INR",
        qty=10.0,
        entry_price=10.0,
        entry_time=now,
        mode=BotMode.PAPER,
        status=PositionStatus.OPEN,
        current_price=10.0,
        stop_loss=8.0,
        take_profit=15.0,
    )
    pos_valid = Position(
        id="pos-valid-coin",
        bot=BotName.HDA,
        coin="DOGE",
        pair="DOGE/INR",
        qty=50.0,
        entry_price=10.0,
        entry_time=now,
        mode=BotMode.PAPER,
        status=PositionStatus.OPEN,
        current_price=10.0,
        stop_loss=8.0,
        take_profit=15.0,
    )
    await pos_repo.insert(pos_missing)
    await pos_repo.insert(pos_valid)

    # Only provide ticker for DOGE
    await trading_svc.check_open_position_exits({"DOGE": 12.0})

    p_missing = await pos_repo.get_by_id("pos-missing-coin")
    p_valid = await pos_repo.get_by_id("pos-valid-coin")

    assert p_missing.current_price == 10.0  # Preserved
    assert p_valid.current_price == 12.0  # Updated
    assert p_valid.unrealised_pnl == 100.0  # (12 - 10) * 50


# ── Test I: PositionRepository.get_open() returns updated prices ─────────────


@pytest.mark.anyio
async def test_i_get_open_returns_updated_mark_prices(setup_env):
    env = setup_env
    pos_repo: PositionRepository = env["pos_repo"]
    trading_svc: TradingService = env["trading_svc"]

    now = datetime.now(timezone.utc)
    pos = Position(
        id="pos-getopen-01",
        bot=BotName.VCP,
        coin="NEAR",
        pair="NEAR/INR",
        qty=4.0,
        entry_price=300.0,
        entry_time=now,
        mode=BotMode.PAPER,
        status=PositionStatus.OPEN,
        current_price=300.0,
        stop_loss=280.0,
        take_profit=350.0,
    )
    await pos_repo.insert(pos)

    await trading_svc.check_open_position_exits({"NEAR": 320.0})

    open_positions = await pos_repo.get_open()
    target_pos = next((p for p in open_positions if p.id == "pos-getopen-01"), None)
    assert target_pos is not None
    assert target_pos.current_price == 320.0
    assert target_pos.unrealised_pnl == 80.0  # (320 - 300) * 4


# ── Test J: PortfolioService.get_snapshot() aggregates total_unrealised_pnl ─


@pytest.mark.anyio
async def test_j_portfolio_service_snapshot_aggregates_unrealized(setup_env):
    env = setup_env
    pos_repo: PositionRepository = env["pos_repo"]
    trading_svc: TradingService = env["trading_svc"]
    portfolio_svc: PortfolioService = env["portfolio_svc"]

    now = datetime.now(timezone.utc)
    pos1 = Position(
        id="pos-port-01",
        bot=BotName.STE,
        coin="BTC",
        pair="BTC/INR",
        qty=0.01,
        entry_price=1000.0,
        entry_time=now,
        mode=BotMode.PAPER,
        status=PositionStatus.OPEN,
        current_price=1000.0,
        stop_loss=900.0,
        take_profit=1500.0,
    )
    pos2 = Position(
        id="pos-port-02",
        bot=BotName.HDA,
        coin="ETH",
        pair="ETH/INR",
        qty=0.1,
        entry_price=500.0,
        entry_time=now,
        mode=BotMode.PAPER,
        status=PositionStatus.OPEN,
        current_price=500.0,
        stop_loss=400.0,
        take_profit=700.0,
    )
    await pos_repo.insert(pos1)
    await pos_repo.insert(pos2)

    # BTC price moves to 1050 (+50 * 0.01 = +0.50)
    # ETH price moves to 495 (-5 * 0.1 = -0.50, within 3% trailing stop)
    await trading_svc.check_open_position_exits({"BTC": 1050.0, "ETH": 495.0})

    snapshot = await portfolio_svc.get_snapshot()
    # Total unrealised PnL = +0.50 + (-0.50) = 0.0
    assert snapshot.total_unrealised_pnl == 0.0

    # Next update: BTC rises to 1200 (+200 * 0.01 = +2.0)
    # ETH recovers to 510 (+10 * 0.1 = +1.0)
    await trading_svc.check_open_position_exits({"BTC": 1200.0, "ETH": 510.0})
    snapshot2 = await portfolio_svc.get_snapshot()
    assert snapshot2.total_unrealised_pnl == 3.0


# ── Test K/L/M: Immutability of core fields ──────────────────────────────────


@pytest.mark.anyio
async def test_k_l_m_core_fields_remain_immutable(setup_env):
    env = setup_env
    pos_repo: PositionRepository = env["pos_repo"]
    trading_svc: TradingService = env["trading_svc"]

    now = datetime.now(timezone.utc)
    pos = Position(
        id="pos-immutable-01",
        bot=BotName.STE,
        coin="SOL",
        pair="SOL/INR",
        qty=1.234,
        entry_price=15000.0,
        entry_time=now,
        mode=BotMode.PAPER,
        status=PositionStatus.OPEN,
        current_price=15000.0,
        stop_loss=14000.0,
        take_profit=17000.0,
    )
    await pos_repo.insert(pos)

    # Apply 3 price updates
    for p in [15200.0, 15400.0, 15300.0]:
        await trading_svc.check_open_position_exits({"SOL": p})

    db_pos = await pos_repo.get_by_id("pos-immutable-01")
    assert db_pos is not None
    # Verify core fields remain completely untouched
    assert db_pos.entry_price == 15000.0
    assert db_pos.qty == 1.234
    assert db_pos.stop_loss == 14000.0
    assert db_pos.take_profit == 17000.0
    assert db_pos.id == "pos-immutable-01"
    assert db_pos.coin == "SOL"
    assert db_pos.pair == "SOL/INR"
    assert db_pos.mode == BotMode.PAPER
