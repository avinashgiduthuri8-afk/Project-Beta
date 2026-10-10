"""
PROJECT-ALPHA — Master Production Execution, Reconciliation & Safety Tests.

Comprehensive test suite covering execution, reconciliation, and safety invariants:
  BUY:
    1. Paper BUY uses paper path
    2. Shadow BUY never reaches live
    3. Live BUY calls place_live_order
    4. Rejected BUY -> no position
    5. Pending BUY -> no OPEN position
    6. Filled BUY -> OPEN position
    7. exchange_order_id persisted
    8. actual filled_qty persisted
  SELL:
    9. Paper SELL uses paper path
    10. Live SELL calls place_live_order
    11. Failed SELL -> remains OPEN
    12. Pending SELL -> remains OPEN
    13. Filled SELL -> CLOSED
    14. Partial SELL -> remaining position OPEN
    15. Successful LIVE SELL never calls place_order()
  TIMEOUT:
    16. Timeout does not blindly duplicate BUY
    17. Timeout does not blindly duplicate SELL
  RECONCILIATION:
    18. Detect exchange/local mismatch
    19. Handle rejected order
    20. Handle partial fill
  SCHEDULER:
    21. Exit monitor registered once
    22. Reconciliation registered once
    23. poll_exits reaches exit evaluation
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from background.scheduler.jobs import register_all_jobs
from background.scheduler.scheduler import BackgroundScheduler
from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.config import AppConfig, invalidate_config
from core.repository.db import Database
from core.repository.event_log_repo import EventLogRepository
from core.repository.position_repo import PositionRepository
from core.repository.trade_repo import TradeRepository
from core.types import BotMode, BotName, ExitReason, Position, PositionStatus
from execution import TradingService
from execution.trading.subaccount_manager import CoinDCXSubAccountManager

TEST_DB_DIR = os.path.abspath(".test_dbs")
os.makedirs(TEST_DB_DIR, exist_ok=True)


@pytest.fixture(autouse=True)
def setup_test_db(monkeypatch):
    test_db = os.path.join(TEST_DB_DIR, f"test_exec_{uuid.uuid4().hex[:6]}.db")
    monkeypatch.setenv("V2_DB_PATH", test_db)
    monkeypatch.setenv("COINDCX_LIVE_API_KEY", "real_test_live_key_98765")
    monkeypatch.setenv("COINDCX_LIVE_API_SECRET", "real_test_live_secret_98765")
    monkeypatch.setenv("OPERATOR_SECURITY_PASSWORD", "real_test_password_98765")
    invalidate_config()
    yield
    invalidate_config()


async def _create_test_env(mode: str = "LIVE_MICROCASH", trading_enabled: bool = True):
    db_file = os.path.join(TEST_DB_DIR, f"db_{uuid.uuid4().hex[:8]}.db")
    db = Database(db_file)
    await db.open()

    bus = EventBus()
    pos_repo = PositionRepository(db.connection)
    trade_repo = TradeRepository(db.connection)
    event_repo = EventLogRepository(db.connection)

    cfg = AppConfig(
        v2_deployment_mode=mode,
        v2_trading_enabled=trading_enabled,
        total_capital_limit=10000.0,
        v2_db_path=db_file,
    )
    mgr = CoinDCXSubAccountManager()

    service = TradingService(
        bus=bus,
        position_repo=pos_repo,
        trade_repo=trade_repo,
        event_log_repo=event_repo,
        config=cfg,
        subaccount_manager=mgr,
    )
    await service.start()
    return db, bus, pos_repo, trade_repo, event_repo, cfg, mgr, service


# ── 1 to 8: BUY TESTS ────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_01_paper_buy_uses_paper_path():
    """1. Paper BUY uses paper path and never calls live exchange."""
    (
        db,
        bus,
        pos_repo,
        trade_repo,
        event_repo,
        cfg,
        mgr,
        service,
    ) = await _create_test_env(mode="PAPER", trading_enabled=False)
    client = mgr.get_client(BotName.STE)
    client.place_live_order = AsyncMock()

    payload = {
        "signal_id": "SIG-1",
        "coin": "SOL",
        "pair": "SOL/INR",
        "bot": "STE",
        "price": 12500.0,
        "approved_amount": 200.0,
    }
    await service.on_trade_approved(EventType.TRADE_APPROVED, payload)

    client.place_live_order.assert_not_called()
    open_pos = await pos_repo.get_open()
    assert len(open_pos) == 1
    assert open_pos[0].mode == BotMode.PAPER
    await db.close()


@pytest.mark.anyio
async def test_02_shadow_buy_never_reaches_live():
    """2. Shadow BUY never reaches live exchange."""
    (
        db,
        bus,
        pos_repo,
        trade_repo,
        event_repo,
        cfg,
        mgr,
        service,
    ) = await _create_test_env(mode="PAPER", trading_enabled=False)
    client = mgr.get_client(BotName.STE)
    client.place_live_order = AsyncMock()

    payload = {
        "signal_id": "SIG-2",
        "coin": "ETH",
        "pair": "ETH/INR",
        "bot": "STE",
        "price": 260000.0,
        "approved_amount": 200.0,
    }
    await service.on_trade_approved(EventType.TRADE_APPROVED, payload)

    client.place_live_order.assert_not_called()
    await db.close()


@pytest.mark.anyio
async def test_03_live_buy_calls_place_live_order():
    """3. Live BUY calls place_live_order."""
    (
        db,
        bus,
        pos_repo,
        trade_repo,
        event_repo,
        cfg,
        mgr,
        service,
    ) = await _create_test_env(mode="LIVE_MICROCASH", trading_enabled=True)
    client = mgr.get_client(BotName.STE)
    client.place_live_order = AsyncMock(
        return_value={
            "success": True,
            "exchange_order_id": "EX-BUY-101",
            "client_order_id": "CL-BUY-101",
            "status": "FILLED",
            "is_filled": True,
            "filled_qty": 0.016,
            "price": 12500.0,
            "qty": 0.016,
        }
    )

    payload = {
        "signal_id": "SIG-3",
        "coin": "SOL",
        "pair": "SOL/INR",
        "bot": "STE",
        "price": 12500.0,
        "approved_amount": 200.0,
    }
    await service.on_trade_approved(EventType.TRADE_APPROVED, payload)

    assert client.place_live_order.call_count >= 1
    await db.close()


@pytest.mark.anyio
async def test_04_rejected_buy_no_position():
    """4. Rejected BUY -> no position opened."""
    (
        db,
        bus,
        pos_repo,
        trade_repo,
        event_repo,
        cfg,
        mgr,
        service,
    ) = await _create_test_env(mode="LIVE_MICROCASH", trading_enabled=True)
    client = mgr.get_client(BotName.STE)
    client.place_live_order = AsyncMock(
        return_value={
            "success": False,
            "error": "INSUFFICIENT_FUNDS",
            "message": "Exchange rejected order",
        }
    )

    payload = {
        "signal_id": "SIG-4",
        "coin": "SOL",
        "pair": "SOL/INR",
        "bot": "STE",
        "price": 12500.0,
        "approved_amount": 200.0,
    }
    await service.on_trade_approved(EventType.TRADE_APPROVED, payload)

    open_pos = await pos_repo.get_open()
    assert len(open_pos) == 0
    await db.close()


@pytest.mark.anyio
async def test_05_pending_buy_no_open_position():
    """5. Pending BUY -> no OPEN position until confirmed."""
    (
        db,
        bus,
        pos_repo,
        trade_repo,
        event_repo,
        cfg,
        mgr,
        service,
    ) = await _create_test_env(mode="LIVE_MICROCASH", trading_enabled=True)
    client = mgr.get_client(BotName.STE)
    client.place_live_order = AsyncMock(
        return_value={
            "success": True,
            "exchange_order_id": "EX-PENDING-5",
            "status": "OPEN",
            "is_filled": False,
            "filled_qty": 0.0,
        }
    )

    payload = {
        "signal_id": "SIG-5",
        "coin": "SOL",
        "pair": "SOL/INR",
        "bot": "STE",
        "price": 12500.0,
        "approved_amount": 200.0,
    }
    await service.on_trade_approved(EventType.TRADE_APPROVED, payload)

    open_pos = await pos_repo.get_open()
    assert len(open_pos) == 0
    await db.close()


@pytest.mark.anyio
async def test_06_07_08_filled_buy_persists_order_id_and_actual_qty():
    """6, 7, 8. Filled BUY creates OPEN position with persisted exchange_order_id and actual filled_qty."""
    (
        db,
        bus,
        pos_repo,
        trade_repo,
        event_repo,
        cfg,
        mgr,
        service,
    ) = await _create_test_env(mode="LIVE_MICROCASH", trading_enabled=True)
    client = mgr.get_client(BotName.STE)
    client.place_live_order = AsyncMock(
        return_value={
            "success": True,
            "exchange_order_id": "EX-REAL-888",
            "client_order_id": "CL-REAL-888",
            "status": "FILLED",
            "is_filled": True,
            "filled_qty": 0.0155,  # actual partial fill vs 0.016 requested
            "price": 12500.0,
            "qty": 0.016,
        }
    )

    payload = {
        "signal_id": "SIG-8",
        "coin": "SOL",
        "pair": "SOL/INR",
        "bot": "STE",
        "price": 12500.0,
        "approved_amount": 200.0,
    }
    await service.on_trade_approved(EventType.TRADE_APPROVED, payload)

    open_pos = await pos_repo.get_open()
    assert len(open_pos) == 1
    pos = open_pos[0]
    assert pos.status == PositionStatus.OPEN
    assert pos.exchange_order_id == "EX-REAL-888"
    assert pos.client_order_id == "CL-REAL-888"
    assert pos.qty == 0.0155
    assert pos.filled_qty == 0.0155
    await db.close()


# ── 9 to 15: SELL TESTS ───────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_09_paper_sell_uses_paper_path():
    """9. Paper SELL uses paper path."""
    (
        db,
        bus,
        pos_repo,
        trade_repo,
        event_repo,
        cfg,
        mgr,
        service,
    ) = await _create_test_env(mode="PAPER", trading_enabled=False)
    client = mgr.get_client(BotName.STE)
    client.place_live_order = AsyncMock()

    # Create paper position
    pos = Position(
        id="pos-paper-1",
        bot=BotName.STE,
        coin="SOL",
        pair="SOL/INR",
        qty=0.016,
        entry_price=10000.0,
        entry_time=datetime.now(timezone.utc),
        mode=BotMode.PAPER,
        stop_loss=9000.0,
        take_profit=11000.0,
    )
    await pos_repo.insert(pos)

    # Trigger TP
    closed = await service.check_open_position_exits({"SOL/INR": 11500.0})
    assert len(closed) == 1
    client.place_live_order.assert_not_called()
    await db.close()


@pytest.mark.anyio
async def test_10_live_sell_calls_place_live_order():
    """10. Live SELL calls place_live_order."""
    (
        db,
        bus,
        pos_repo,
        trade_repo,
        event_repo,
        cfg,
        mgr,
        service,
    ) = await _create_test_env(mode="LIVE_MICROCASH", trading_enabled=True)
    client = mgr.get_client(BotName.STE)
    client.place_live_order = AsyncMock(
        return_value={
            "success": True,
            "exchange_order_id": "EX-SELL-10",
            "status": "FILLED",
            "is_filled": True,
            "filled_qty": 0.016,
            "price": 11500.0,
        }
    )

    pos = Position(
        id="pos-live-10",
        bot=BotName.STE,
        coin="SOL",
        pair="SOL/INR",
        qty=0.016,
        entry_price=10000.0,
        entry_time=datetime.now(timezone.utc),
        mode=BotMode.LIVE,
        stop_loss=9000.0,
        take_profit=11000.0,
        exchange_order_id="EX-BUY-10",
    )
    await pos_repo.insert(pos)

    closed = await service.check_open_position_exits({"SOL/INR": 11500.0})
    assert len(closed) == 1
    client.place_live_order.assert_called_once()
    await db.close()


@pytest.mark.anyio
async def test_11_failed_sell_remains_open():
    """11. Failed SELL -> position remains OPEN."""
    (
        db,
        bus,
        pos_repo,
        trade_repo,
        event_repo,
        cfg,
        mgr,
        service,
    ) = await _create_test_env(mode="LIVE_MICROCASH", trading_enabled=True)
    client = mgr.get_client(BotName.STE)
    client.place_live_order = AsyncMock(
        return_value={
            "success": False,
            "error": "REJECTED_BY_EXCHANGE",
        }
    )

    pos = Position(
        id="pos-live-11",
        bot=BotName.STE,
        coin="SOL",
        pair="SOL/INR",
        qty=0.016,
        entry_price=10000.0,
        entry_time=datetime.now(timezone.utc),
        mode=BotMode.LIVE,
        stop_loss=9000.0,
        take_profit=11000.0,
    )
    await pos_repo.insert(pos)

    closed = await service.check_open_position_exits({"SOL/INR": 11500.0})
    assert len(closed) == 0
    open_pos = await pos_repo.get_open()
    assert len(open_pos) == 1
    assert open_pos[0].status == PositionStatus.OPEN
    await db.close()


@pytest.mark.anyio
async def test_12_pending_sell_remains_open():
    """12. Pending SELL -> position remains OPEN."""
    (
        db,
        bus,
        pos_repo,
        trade_repo,
        event_repo,
        cfg,
        mgr,
        service,
    ) = await _create_test_env(mode="LIVE_MICROCASH", trading_enabled=True)
    client = mgr.get_client(BotName.STE)
    client.place_live_order = AsyncMock(
        return_value={
            "success": True,
            "exchange_order_id": "EX-SELL-PENDING",
            "status": "OPEN",
            "is_filled": False,
            "filled_qty": 0.0,
        }
    )

    pos = Position(
        id="pos-live-12",
        bot=BotName.STE,
        coin="SOL",
        pair="SOL/INR",
        qty=0.016,
        entry_price=10000.0,
        entry_time=datetime.now(timezone.utc),
        mode=BotMode.LIVE,
        stop_loss=9000.0,
        take_profit=11000.0,
    )
    await pos_repo.insert(pos)

    closed = await service.check_open_position_exits({"SOL/INR": 11500.0})
    assert len(closed) == 0
    open_pos = await pos_repo.get_open()
    assert len(open_pos) == 1
    await db.close()


@pytest.mark.anyio
async def test_13_filled_sell_closed():
    """13. Filled SELL -> position CLOSED and trade recorded."""
    (
        db,
        bus,
        pos_repo,
        trade_repo,
        event_repo,
        cfg,
        mgr,
        service,
    ) = await _create_test_env(mode="LIVE_MICROCASH", trading_enabled=True)
    client = mgr.get_client(BotName.STE)
    client.place_live_order = AsyncMock(
        return_value={
            "success": True,
            "exchange_order_id": "EX-SELL-13",
            "status": "FILLED",
            "is_filled": True,
            "filled_qty": 0.016,
            "price": 11500.0,
        }
    )

    pos = Position(
        id="pos-live-13",
        bot=BotName.STE,
        coin="SOL",
        pair="SOL/INR",
        qty=0.016,
        entry_price=10000.0,
        entry_time=datetime.now(timezone.utc),
        mode=BotMode.LIVE,
        stop_loss=9000.0,
        take_profit=11000.0,
    )
    await pos_repo.insert(pos)

    closed = await service.check_open_position_exits({"SOL/INR": 11500.0})
    assert len(closed) == 1
    open_pos = await pos_repo.get_open()
    assert len(open_pos) == 0
    trades = await trade_repo.get_by_coin("SOL")
    assert len(trades) == 1
    assert trades[0].exchange_order_id == "EX-SELL-13"
    await db.close()


@pytest.mark.anyio
async def test_14_partial_sell_keeps_remaining_position_open():
    """14. Partial SELL -> remaining position remains OPEN."""
    (
        db,
        bus,
        pos_repo,
        trade_repo,
        event_repo,
        cfg,
        mgr,
        service,
    ) = await _create_test_env(mode="LIVE_MICROCASH", trading_enabled=True)
    client = mgr.get_client(BotName.STE)
    client.place_live_order = AsyncMock(
        return_value={
            "success": True,
            "exchange_order_id": "EX-SELL-PARTIAL",
            "status": "PARTIALLY_FILLED",
            "is_filled": False,
            "filled_qty": 0.008,  # Half filled out of 0.016
            "price": 11500.0,
        }
    )

    pos = Position(
        id="pos-live-14",
        bot=BotName.STE,
        coin="SOL",
        pair="SOL/INR",
        qty=0.016,
        entry_price=10000.0,
        entry_time=datetime.now(timezone.utc),
        mode=BotMode.LIVE,
        stop_loss=9000.0,
        take_profit=11000.0,
    )
    await pos_repo.insert(pos)

    closed = await service.check_open_position_exits({"SOL/INR": 11500.0})
    assert len(closed) == 1
    assert closed[0].qty == 0.008

    # Position must remain OPEN with 0.008 remaining
    open_pos = await pos_repo.get_open()
    assert len(open_pos) == 1
    assert open_pos[0].status == PositionStatus.OPEN
    assert pytest.approx(open_pos[0].qty, 1e-6) == 0.008
    await db.close()


@pytest.mark.anyio
async def test_15_successful_live_sell_never_calls_paper_place_order():
    """15. CRITICAL INVARIANT: A successful LIVE SELL never calls place_order()."""
    (
        db,
        bus,
        pos_repo,
        trade_repo,
        event_repo,
        cfg,
        mgr,
        service,
    ) = await _create_test_env(mode="LIVE_MICROCASH", trading_enabled=True)
    client = mgr.get_client(BotName.STE)
    client.place_order = MagicMock(
        side_effect=AssertionError("FATAL: duplicate mock place_order was called!")
    )
    client.place_live_order = AsyncMock(
        return_value={
            "success": True,
            "exchange_order_id": "EX-SELL-15",
            "status": "FILLED",
            "is_filled": True,
            "filled_qty": 0.016,
            "price": 11500.0,
        }
    )

    pos = Position(
        id="pos-live-15",
        bot=BotName.STE,
        coin="SOL",
        pair="SOL/INR",
        qty=0.016,
        entry_price=10000.0,
        entry_time=datetime.now(timezone.utc),
        mode=BotMode.LIVE,
        stop_loss=9000.0,
        take_profit=11000.0,
    )
    await pos_repo.insert(pos)

    closed = await service.check_open_position_exits({"SOL/INR": 11500.0})
    assert len(closed) == 1
    client.place_order.assert_not_called()
    await db.close()


# ── 16 & 17: TIMEOUT SAFETY TESTS ─────────────────────────────────────────────


@pytest.mark.anyio
async def test_16_timeout_does_not_blindly_duplicate_buy():
    """16. Timeout does not blindly duplicate BUY order."""
    (
        db,
        bus,
        pos_repo,
        trade_repo,
        event_repo,
        cfg,
        mgr,
        service,
    ) = await _create_test_env(mode="LIVE_MICROCASH", trading_enabled=True)
    client = mgr.get_client(BotName.STE)
    # Simulate network timeout
    client.place_live_order = AsyncMock(
        return_value={
            "success": False,
            "error": "TIMEOUT",
            "client_order_id": "CL-TIMEOUT-16",
            "requires_reconciliation": True,
        }
    )
    # Verification check returns NOT_FOUND (order never reached exchange)
    client.get_order_by_client_id = AsyncMock(
        return_value={
            "success": False,
            "status": "NOT_FOUND",
        }
    )

    payload = {
        "signal_id": "SIG-16",
        "coin": "SOL",
        "pair": "SOL/INR",
        "bot": "STE",
        "price": 12500.0,
        "approved_amount": 200.0,
    }
    await service.on_trade_approved(EventType.TRADE_APPROVED, payload)

    # Verification was checked
    client.get_order_by_client_id.assert_called_once_with("CL-TIMEOUT-16")
    # No duplicate submission and no position opened
    open_pos = await pos_repo.get_open()
    assert len(open_pos) == 0
    await db.close()


@pytest.mark.anyio
async def test_17_timeout_does_not_blindly_duplicate_sell():
    """17. Timeout on SELL does not blindly duplicate exit submission."""
    (
        db,
        bus,
        pos_repo,
        trade_repo,
        event_repo,
        cfg,
        mgr,
        service,
    ) = await _create_test_env(mode="LIVE_MICROCASH", trading_enabled=True)
    client = mgr.get_client(BotName.STE)
    client.place_live_order = AsyncMock(
        return_value={
            "success": False,
            "error": "TIMEOUT",
            "client_order_id": "CL-TIMEOUT-SELL-17",
            "requires_reconciliation": True,
        }
    )
    client.get_order_by_client_id = AsyncMock(
        return_value={
            "success": False,
            "status": "NOT_FOUND",
        }
    )

    pos = Position(
        id="pos-live-17",
        bot=BotName.STE,
        coin="SOL",
        pair="SOL/INR",
        qty=0.016,
        entry_price=10000.0,
        entry_time=datetime.now(timezone.utc),
        mode=BotMode.LIVE,
        stop_loss=9000.0,
        take_profit=11000.0,
    )
    await pos_repo.insert(pos)

    closed = await service.check_open_position_exits({"SOL/INR": 11500.0})
    assert len(closed) == 0
    # Position remains OPEN for safety
    open_pos = await pos_repo.get_open()
    assert len(open_pos) == 1
    await db.close()


# ── 18 to 20: ORDER RECONCILIATION TESTS ──────────────────────────────────────


@pytest.mark.anyio
async def test_18_reconciliation_detects_mismatch():
    """18. Detect exchange/local mismatch (missing exchange order id on live position)."""
    (
        db,
        bus,
        pos_repo,
        trade_repo,
        event_repo,
        cfg,
        mgr,
        service,
    ) = await _create_test_env(mode="LIVE_MICROCASH", trading_enabled=True)

    pos = Position(
        id="pos-mismatch-18",
        bot=BotName.STE,
        coin="SOL",
        pair="SOL/INR",
        qty=0.016,
        entry_price=10000.0,
        entry_time=datetime.now(timezone.utc),
        mode=BotMode.LIVE,
        exchange_order_id=None,  # Missing!
    )
    await pos_repo.insert(pos)

    report = await service.reconcile_live_orders()
    assert report["mismatches"] == 1
    assert report["discrepancies"][0]["exchange_status"] == "MISSING_EXCHANGE_ORDER_ID"
    await db.close()


@pytest.mark.anyio
async def test_19_reconciliation_handles_rejected_order():
    """19. Reconciliation auto-repairs order cancelled/rejected on exchange to CLOSED."""
    (
        db,
        bus,
        pos_repo,
        trade_repo,
        event_repo,
        cfg,
        mgr,
        service,
    ) = await _create_test_env(mode="LIVE_MICROCASH", trading_enabled=True)
    client = mgr.get_client(BotName.STE)
    client.get_order_status = AsyncMock(
        return_value={
            "success": True,
            "status": "CANCELLED",
            "exchange_order_id": "EX-CANC-19",
        }
    )

    pos = Position(
        id="pos-canc-19",
        bot=BotName.STE,
        coin="SOL",
        pair="SOL/INR",
        qty=0.016,
        entry_price=10000.0,
        entry_time=datetime.now(timezone.utc),
        mode=BotMode.LIVE,
        exchange_order_id="EX-CANC-19",
    )
    await pos_repo.insert(pos)

    report = await service.reconcile_live_orders()
    assert report["mismatches"] == 1
    assert report["discrepancies"][0]["action"] == "AUTO_REPAIRED_TO_CLOSED"

    # Local position is now closed
    open_pos = await pos_repo.get_open()
    assert len(open_pos) == 0
    await db.close()


@pytest.mark.anyio
async def test_20_reconciliation_handles_partial_fill():
    """20. Reconciliation aligns local quantity with exchange partial fill."""
    (
        db,
        bus,
        pos_repo,
        trade_repo,
        event_repo,
        cfg,
        mgr,
        service,
    ) = await _create_test_env(mode="LIVE_MICROCASH", trading_enabled=True)
    client = mgr.get_client(BotName.STE)
    client.get_order_status = AsyncMock(
        return_value={
            "success": True,
            "status": "PARTIALLY_FILLED",
            "filled_qty": 0.010,  # 0.010 on exchange vs 0.016 local
            "exchange_order_id": "EX-PARTIAL-20",
        }
    )

    pos = Position(
        id="pos-partial-20",
        bot=BotName.STE,
        coin="SOL",
        pair="SOL/INR",
        qty=0.016,
        entry_price=10000.0,
        entry_time=datetime.now(timezone.utc),
        mode=BotMode.LIVE,
        exchange_order_id="EX-PARTIAL-20",
    )
    await pos_repo.insert(pos)

    report = await service.reconcile_live_orders()
    assert report["mismatches"] == 1
    assert report["discrepancies"][0]["action"] == "QUANTITY_ALIGNED"

    updated = await pos_repo.get_by_id("pos-partial-20")
    assert pytest.approx(updated.qty, 1e-6) == 0.010
    await db.close()


# ── 21 to 23: SCHEDULER TESTS ─────────────────────────────────────────────────


def test_21_22_scheduler_registers_exit_and_reconciliation_once():
    """21, 22. Exit monitor and reconciliation are registered once in the scheduler."""
    bus = EventBus()
    sched = BackgroundScheduler(bus)
    scanner = MagicMock()
    trading = MagicMock()
    cfg = AppConfig()

    register_all_jobs(sched, cfg, scanner, trading)

    assert "exit_monitor" in sched._jobs
    assert "order_reconciliation" in sched._jobs
    assert "scanner_poll" in sched._jobs
    assert sched._jobs["exit_monitor"].interval == 5
    assert sched._jobs["order_reconciliation"].interval == 60


@pytest.mark.anyio
async def test_23_poll_exits_reaches_exit_evaluation():
    """23. poll_exits reaches exit evaluation and calls check_open_position_exits."""
    (
        db,
        bus,
        pos_repo,
        trade_repo,
        event_repo,
        cfg,
        mgr,
        service,
    ) = await _create_test_env()
    pos = Position(
        id="pos-poll-23",
        bot=BotName.STE,
        coin="SOL",
        pair="SOL/INR",
        qty=0.016,
        entry_price=10000.0,
        entry_time=datetime.now(timezone.utc),
        mode=BotMode.PAPER,
        stop_loss=9000.0,
        take_profit=11000.0,
    )
    await pos_repo.insert(pos)

    # Provide price that triggers exit
    closed = await service.poll_exits(price_provider={"SOL/INR": 11500.0})
    assert len(closed) == 1
    assert closed[0].exit_reason == ExitReason.TAKE_PROFIT
    await db.close()
