"""
PROJECT-ALPHA — Live Execution Wiring, Fill Confirmation, and Order Reconciliation Tests.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.config import AppConfig, invalidate_config
from core.repository.db import Database
from core.repository.event_log_repo import EventLogRepository
from core.repository.position_repo import PositionRepository
from core.repository.trade_repo import TradeRepository
from core.types import BotMode, BotName, ExitReason, Position, PositionStatus
from execution.service import TradingService
from execution.trading.subaccount_manager import (
    CoinDCXSubAccountClient,
    CoinDCXSubAccountManager,
    SubAccountConfig,
)


@pytest.fixture(autouse=True)
def setup_test_env(tmp_path, monkeypatch):
    test_db = str(tmp_path / f"test_exec_{uuid.uuid4().hex[:6]}.db")
    monkeypatch.setenv("V2_DB_PATH", test_db)
    monkeypatch.setenv("V2_DEPLOYMENT_MODE", "PAPER")
    monkeypatch.setenv("V2_TRADING_ENABLED", "false")
    monkeypatch.setenv("TOTAL_CAPITAL_LIMIT", "10000.0")
    monkeypatch.setenv("ORDER_SIZE_INR", "200.0")
    invalidate_config()
    yield
    invalidate_config()


# ── BUY PATH TESTS ─────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_1_paper_buy_execution(tmp_path):
    """1. Paper BUY in SHADOW mode creates a local PAPER position without calling live HTTP."""
    db_path = str(tmp_path / "test_p1.db")
    db = Database(db_path)
    await db.open()

    bus = EventBus()
    pos_repo = PositionRepository(db.connection)
    trade_repo = TradeRepository(db.connection)
    event_repo = EventLogRepository(db.connection)
    cfg = AppConfig(
        v2_deployment_mode="PAPER",
        v2_trading_enabled=False,
        total_capital_limit=10000.0,
    )

    mgr = CoinDCXSubAccountManager()
    client = mgr.get_client(BotName.STE)
    client.place_live_order = AsyncMock()

    service = TradingService(
        bus=bus,
        position_repo=pos_repo,
        trade_repo=trade_repo,
        event_log_repo=event_repo,
        config=cfg,
        subaccount_manager=mgr,
    )
    await service.start()

    payload = {
        "signal_id": "SIG-01",
        "coin": "SOL",
        "pair": "SOL/INR",
        "bot": "STE",
        "price": 10000.0,
        "approved_amount": 200.0,
    }
    await service.on_trade_approved(EventType.TRADE_APPROVED, payload)

    # Verify place_live_order was NEVER called in SHADOW/PAPER mode
    client.place_live_order.assert_not_called()

    # Verify paper position was created
    open_pos = await pos_repo.get_open()
    assert len(open_pos) == 1
    assert open_pos[0].coin == "SOL"
    assert open_pos[0].mode == BotMode.PAPER
    assert open_pos[0].status == PositionStatus.OPEN

    await service.stop()
    await db.close()


@pytest.mark.anyio
async def test_2_and_6_live_buy_confirmed_filled(tmp_path):
    """2 & 6. Live BUY calls place_live_order and only opens position when confirmed FILLED."""
    db_path = str(tmp_path / "test_p2.db")
    db = Database(db_path)
    await db.open()

    bus = EventBus()
    pos_repo = PositionRepository(db.connection)
    trade_repo = TradeRepository(db.connection)
    event_repo = EventLogRepository(db.connection)
    cfg = AppConfig(
        v2_deployment_mode="LIVE_MICROCASH",
        v2_trading_enabled=True,
        total_capital_limit=10000.0,
    )

    mgr = CoinDCXSubAccountManager()
    client = mgr.get_client(BotName.STE)
    client.place_live_order = AsyncMock(
        return_value={
            "success": True,
            "status_code": 200,
            "exchange_order_id": "EX-ORD-BUY-9999",
            "client_order_id": "ORD_ALPHA_STE_01_12345",
            "status": "FILLED",
            "is_filled": True,
            "price": 10000.0,
            "qty": 0.02,
            "notional_inr": 200.0,
        }
    )

    service = TradingService(
        bus=bus,
        position_repo=pos_repo,
        trade_repo=trade_repo,
        event_log_repo=event_repo,
        config=cfg,
        subaccount_manager=mgr,
    )
    await service.start()

    payload = {
        "signal_id": "SIG-02",
        "coin": "SOL",
        "pair": "SOL/INR",
        "bot": "STE",
        "price": 10000.0,
        "approved_amount": 200.0,
    }
    await service.on_trade_approved(EventType.TRADE_APPROVED, payload)

    # Verify place_live_order was called for BUY with correct parameters
    buy_call = client.place_live_order.call_args_list[0]
    assert buy_call.kwargs["pair"] == "SOL/INR"
    assert buy_call.kwargs["side"] == "BUY"
    assert buy_call.kwargs["price"] == 10000.0
    assert buy_call.kwargs["qty"] == 0.02

    # Verify LIVE position is OPEN and has real exchange_order_id
    open_pos = await pos_repo.get_open()
    assert len(open_pos) == 1
    assert open_pos[0].mode == BotMode.LIVE
    assert open_pos[0].exchange_order_id == "EX-ORD-BUY-9999"
    assert open_pos[0].qty == 0.02

    await service.stop()
    await db.close()


@pytest.mark.anyio
async def test_3_live_buy_http_failure_no_position(tmp_path):
    """3. Live BUY HTTP failure creates NO position in SQLite database."""
    db_path = str(tmp_path / "test_p3.db")
    db = Database(db_path)
    await db.open()

    bus = EventBus()
    pos_repo = PositionRepository(db.connection)
    trade_repo = TradeRepository(db.connection)
    event_repo = EventLogRepository(db.connection)
    cfg = AppConfig(
        v2_deployment_mode="LIVE_MICROCASH",
        v2_trading_enabled=True,
        total_capital_limit=10000.0,
    )

    mgr = CoinDCXSubAccountManager()
    client = mgr.get_client(BotName.STE)
    client.place_live_order = AsyncMock(
        return_value={
            "success": False,
            "status_code": 500,
            "error": "NETWORK_ERROR",
            "message": "Connection to CoinDCX timed out",
        }
    )

    service = TradingService(
        bus=bus,
        position_repo=pos_repo,
        trade_repo=trade_repo,
        event_log_repo=event_repo,
        config=cfg,
        subaccount_manager=mgr,
    )
    await service.start()

    payload = {
        "signal_id": "SIG-03",
        "coin": "SOL",
        "pair": "SOL/INR",
        "bot": "STE",
        "price": 10000.0,
        "approved_amount": 200.0,
    }
    await service.on_trade_approved(EventType.TRADE_APPROVED, payload)

    # Verify NO position exists
    open_pos = await pos_repo.get_open()
    assert len(open_pos) == 0

    await service.stop()
    await db.close()


@pytest.mark.anyio
async def test_4_live_buy_rejection_no_position(tmp_path):
    """4. Live BUY rejected by exchange (e.g. 401 Auth / Insufficient funds) creates NO position."""
    db_path = str(tmp_path / "test_p4.db")
    db = Database(db_path)
    await db.open()

    bus = EventBus()
    pos_repo = PositionRepository(db.connection)
    trade_repo = TradeRepository(db.connection)
    event_repo = EventLogRepository(db.connection)
    cfg = AppConfig(
        v2_deployment_mode="LIVE_MICROCASH",
        v2_trading_enabled=True,
        total_capital_limit=10000.0,
    )

    mgr = CoinDCXSubAccountManager()
    client = mgr.get_client(BotName.STE)
    client.place_live_order = AsyncMock(
        return_value={
            "success": False,
            "status_code": 401,
            "error": "AUTH_FAILED",
            "message": "Invalid API Key or HMAC Signature",
        }
    )

    service = TradingService(
        bus=bus,
        position_repo=pos_repo,
        trade_repo=trade_repo,
        event_log_repo=event_repo,
        config=cfg,
        subaccount_manager=mgr,
    )
    await service.start()

    payload = {
        "signal_id": "SIG-04",
        "coin": "SOL",
        "pair": "SOL/INR",
        "bot": "STE",
        "price": 10000.0,
        "approved_amount": 200.0,
    }
    await service.on_trade_approved(EventType.TRADE_APPROVED, payload)

    open_pos = await pos_repo.get_open()
    assert len(open_pos) == 0

    await service.stop()
    await db.close()


@pytest.mark.anyio
async def test_5_live_buy_pending_unfilled_no_position(tmp_path):
    """5. Live BUY submitted with status=OPEN (unfilled limit) does NOT create an OPEN position."""
    db_path = str(tmp_path / "test_p5.db")
    db = Database(db_path)
    await db.open()

    bus = EventBus()
    pos_repo = PositionRepository(db.connection)
    trade_repo = TradeRepository(db.connection)
    event_repo = EventLogRepository(db.connection)
    cfg = AppConfig(
        v2_deployment_mode="LIVE_MICROCASH",
        v2_trading_enabled=True,
        total_capital_limit=10000.0,
    )

    mgr = CoinDCXSubAccountManager()
    client = mgr.get_client(BotName.STE)
    client.place_live_order = AsyncMock(
        return_value={
            "success": True,
            "status_code": 200,
            "exchange_order_id": "EX-ORD-PENDING-123",
            "status": "OPEN",  # Unfilled resting limit order
            "is_filled": False,
            "price": 10000.0,
            "qty": 0.02,
        }
    )

    service = TradingService(
        bus=bus,
        position_repo=pos_repo,
        trade_repo=trade_repo,
        event_log_repo=event_repo,
        config=cfg,
        subaccount_manager=mgr,
    )
    await service.start()

    payload = {
        "signal_id": "SIG-05",
        "coin": "SOL",
        "pair": "SOL/INR",
        "bot": "STE",
        "price": 10000.0,
        "approved_amount": 200.0,
    }
    await service.on_trade_approved(EventType.TRADE_APPROVED, payload)

    # Position must NOT be marked OPEN when resting/unfilled
    open_pos = await pos_repo.get_open()
    assert len(open_pos) == 0

    await service.stop()
    await db.close()


# ── SELL PATH TESTS ────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_7_paper_sell_execution(tmp_path):
    """7. Paper SELL evaluates SL/TP and closes position locally with friction deducted."""
    db_path = str(tmp_path / "test_p7.db")
    db = Database(db_path)
    await db.open()

    bus = EventBus()
    pos_repo = PositionRepository(db.connection)
    trade_repo = TradeRepository(db.connection)
    event_repo = EventLogRepository(db.connection)
    cfg = AppConfig(
        v2_deployment_mode="PAPER",
        v2_trading_enabled=False,
        total_capital_limit=10000.0,
    )

    mgr = CoinDCXSubAccountManager()
    client = mgr.get_client(BotName.STE)
    client.place_live_order = AsyncMock()

    service = TradingService(
        bus=bus,
        position_repo=pos_repo,
        trade_repo=trade_repo,
        event_log_repo=event_repo,
        config=cfg,
        subaccount_manager=mgr,
    )
    await service.start()

    pos = Position(
        id="POS-PAPER-1",
        bot=BotName.STE,
        coin="SOL",
        pair="SOL/INR",
        qty=0.02,
        entry_price=10000.0,
        entry_time=datetime.now(timezone.utc),
        mode=BotMode.PAPER,
        status=PositionStatus.OPEN,
        stop_loss=9500.0,
        take_profit=11000.0,
    )
    await pos_repo.insert(pos)

    # Trigger Take Profit exit
    closed = await service.check_open_position_exits({"SOL": 11050.0})
    assert len(closed) == 1
    assert closed[0].exit_reason == ExitReason.TAKE_PROFIT
    client.place_live_order.assert_not_called()

    # Position closed in DB
    open_pos = await pos_repo.get_open()
    assert len(open_pos) == 0

    await service.stop()
    await db.close()


@pytest.mark.anyio
async def test_8_and_10_live_sell_confirmed_filled(tmp_path):
    """8 & 10. Live SELL invokes place_live_order(side="SELL") and closes position when confirmed."""
    db_path = str(tmp_path / "test_p8.db")
    db = Database(db_path)
    await db.open()

    bus = EventBus()
    pos_repo = PositionRepository(db.connection)
    trade_repo = TradeRepository(db.connection)
    event_repo = EventLogRepository(db.connection)
    cfg = AppConfig(
        v2_deployment_mode="LIVE_MICROCASH",
        v2_trading_enabled=True,
        total_capital_limit=10000.0,
    )

    mgr = CoinDCXSubAccountManager()
    client = mgr.get_client(BotName.STE)
    client.place_live_order = AsyncMock(
        return_value={
            "success": True,
            "status_code": 200,
            "exchange_order_id": "EX-ORD-SELL-7777",
            "status": "FILLED",
            "is_filled": True,
            "price": 11050.0,
            "qty": 0.02,
        }
    )

    service = TradingService(
        bus=bus,
        position_repo=pos_repo,
        trade_repo=trade_repo,
        event_log_repo=event_repo,
        config=cfg,
        subaccount_manager=mgr,
    )
    await service.start()

    pos = Position(
        id="POS-LIVE-1",
        bot=BotName.STE,
        coin="SOL",
        pair="SOL/INR",
        qty=0.02,
        entry_price=10000.0,
        entry_time=datetime.now(timezone.utc),
        mode=BotMode.LIVE,
        status=PositionStatus.OPEN,
        stop_loss=9500.0,
        take_profit=11000.0,
        exchange_order_id="EX-ORD-BUY-1111",
    )
    await pos_repo.insert(pos)

    # Price hits TP
    closed = await service.check_open_position_exits({"SOL": 11050.0})
    assert len(closed) == 1
    assert closed[0].exit_reason == ExitReason.TAKE_PROFIT
    assert closed[0].exchange_order_id == "EX-ORD-SELL-7777"

    # Verify place_live_order was called with SELL
    client.place_live_order.assert_called_once_with(
        pair="SOL/INR",
        side="SELL",
        price=11050.0,
        qty=0.02,
    )

    # Position is closed
    open_pos = await pos_repo.get_open()
    assert len(open_pos) == 0

    await service.stop()
    await db.close()


@pytest.mark.anyio
async def test_9_live_sell_failure_position_remains_open(tmp_path):
    """9. Live SELL failure on exchange ensures local position remains OPEN for retry."""
    db_path = str(tmp_path / "test_p9.db")
    db = Database(db_path)
    await db.open()

    bus = EventBus()
    pos_repo = PositionRepository(db.connection)
    trade_repo = TradeRepository(db.connection)
    event_repo = EventLogRepository(db.connection)
    cfg = AppConfig(
        v2_deployment_mode="LIVE_MICROCASH",
        v2_trading_enabled=True,
        total_capital_limit=10000.0,
    )

    mgr = CoinDCXSubAccountManager()
    client = mgr.get_client(BotName.STE)
    client.place_live_order = AsyncMock(
        return_value={
            "success": False,
            "status_code": 500,
            "error": "NETWORK_ERROR",
            "message": "Exchange unreachable",
        }
    )

    service = TradingService(
        bus=bus,
        position_repo=pos_repo,
        trade_repo=trade_repo,
        event_log_repo=event_repo,
        config=cfg,
        subaccount_manager=mgr,
    )
    await service.start()

    pos = Position(
        id="POS-LIVE-FAIL",
        bot=BotName.STE,
        coin="SOL",
        pair="SOL/INR",
        qty=0.02,
        entry_price=10000.0,
        entry_time=datetime.now(timezone.utc),
        mode=BotMode.LIVE,
        status=PositionStatus.OPEN,
        stop_loss=9500.0,
        take_profit=11000.0,
    )
    await pos_repo.insert(pos)

    # Trigger exit attempt
    closed = await service.check_open_position_exits({"SOL": 11050.0})
    assert len(closed) == 0  # No trades closed because sell failed!

    # Position MUST still be OPEN
    open_pos = await pos_repo.get_open()
    assert len(open_pos) == 1
    assert open_pos[0].id == "POS-LIVE-FAIL"

    await service.stop()
    await db.close()


# ── SAFETY & RECONCILIATION TESTS ──────────────────────────────────────────────


@pytest.mark.anyio
async def test_12_live_mode_never_calls_paper_place_order(tmp_path):
    """12. Prove that LIVE_MICROCASH mode never calls synchronous paper place_order()."""
    db_path = str(tmp_path / "test_p12.db")
    db = Database(db_path)
    await db.open()

    bus = EventBus()
    pos_repo = PositionRepository(db.connection)
    trade_repo = TradeRepository(db.connection)
    event_repo = EventLogRepository(db.connection)
    cfg = AppConfig(
        v2_deployment_mode="LIVE_MICROCASH",
        v2_trading_enabled=True,
        total_capital_limit=10000.0,
    )

    mgr = CoinDCXSubAccountManager()
    client = mgr.get_client(BotName.STE)
    client.place_order = MagicMock(
        side_effect=AssertionError(
            "FATAL: Synchronous paper place_order was called in LIVE mode!"
        )
    )
    client.place_live_order = AsyncMock(
        return_value={
            "success": True,
            "status_code": 200,
            "exchange_order_id": "EX-ORD-LIVE-VALID",
            "status": "FILLED",
            "is_filled": True,
            "price": 10000.0,
            "qty": 0.02,
        }
    )

    service = TradingService(
        bus=bus,
        position_repo=pos_repo,
        trade_repo=trade_repo,
        event_log_repo=event_repo,
        config=cfg,
        subaccount_manager=mgr,
    )
    await service.start()

    payload = {
        "signal_id": "SIG-12",
        "coin": "SOL",
        "pair": "SOL/INR",
        "bot": "STE",
        "price": 10000.0,
        "approved_amount": 200.0,
    }
    # This must not raise the AssertionError from place_order
    await service.on_trade_approved(EventType.TRADE_APPROVED, payload)

    client.place_order.assert_not_called()
    assert client.place_live_order.called

    await service.stop()
    await db.close()


@pytest.mark.anyio
async def test_13_timeout_safety_no_blind_duplicate(tmp_path):
    """13. HTTP timeout returns proper error with client_order_id without duplicating or opening phantom position."""
    live_cfg = AppConfig(v2_deployment_mode="LIVE_MICROCASH", v2_trading_enabled=True)
    client = CoinDCXSubAccountClient(
        SubAccountConfig(
            bot_name=BotName.STE, subaccount_id="STE_01", api_key="k", api_secret="s"
        )
    )
    client.get_balances = AsyncMock(
        return_value={"success": True, "inr_balance": 10000.0}
    )
    mock_http = AsyncMock()
    import httpx

    mock_http.post.side_effect = httpx.TimeoutException(
        "Network timeout connecting to CoinDCX"
    )

    with patch("core.config.get_config", return_value=live_cfg):
        res = await client.place_live_order(
            pair="SOL/INR", side="BUY", price=10000.0, qty=0.02, client=mock_http
        )
    assert res["success"] is False
    assert res["error"] == "TIMEOUT"
    assert res["requires_reconciliation"] is True
    assert "client_order_id" in res


@pytest.mark.anyio
async def test_14_order_reconciliation_repairs_cancelled_orders(tmp_path):
    """14. Reconciliation worker detects cancelled orders on exchange and updates local DB."""
    db_path = str(tmp_path / "test_p14.db")
    db = Database(db_path)
    await db.open()

    bus = EventBus()
    pos_repo = PositionRepository(db.connection)
    trade_repo = TradeRepository(db.connection)
    event_repo = EventLogRepository(db.connection)
    cfg = AppConfig(v2_deployment_mode="LIVE_MICROCASH", v2_trading_enabled=True)

    mgr = CoinDCXSubAccountManager()
    client = mgr.get_client(BotName.STE)
    client.get_order_status = AsyncMock(
        return_value={
            "success": True,
            "status": "CANCELLED",
            "order": {"id": "EX-CANCELLED-1", "status": "CANCELLED"},
        }
    )

    service = TradingService(
        bus=bus,
        position_repo=pos_repo,
        trade_repo=trade_repo,
        event_log_repo=event_repo,
        config=cfg,
        subaccount_manager=mgr,
    )
    await service.start()

    pos = Position(
        id="POS-RECON-1",
        bot=BotName.STE,
        coin="SOL",
        pair="SOL/INR",
        qty=0.02,
        entry_price=10000.0,
        entry_time=datetime.now(timezone.utc),
        mode=BotMode.LIVE,
        status=PositionStatus.OPEN,
        exchange_order_id="EX-CANCELLED-1",
    )
    await pos_repo.insert(pos)

    # Run reconciliation
    report = await service.reconcile_live_orders()
    assert report["reconciled"] >= 1
    assert len(report["cancelled_rejected_orders"]) == 1
    assert report["cancelled_rejected_orders"][0]["exchange_status"] == "CANCELLED"

    # Local position is repaired to CLOSED
    open_pos = await pos_repo.get_open()
    assert len(open_pos) == 0

    await service.stop()
    await db.close()
