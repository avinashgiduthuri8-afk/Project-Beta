"""
V2 Protective Exits Test Suite (P0-03, P0-04, P0-06).

Verifies:
  1. Disaster stop-loss order dispatch on BUY fill (dual-layer stop loss).
  2. Bracket exit priority (cancel resting stop-loss before TP / trailing exit).
  3. Strict manual close verification gate (pending exit, poll verification, safe revert on failure).
  4. Pre-trade slippage and ticker age guards in AutoTradeRouter.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.config import AppConfig
from core.repository.db import Database
from core.repository.event_log_repo import EventLogRepository
from core.repository.order_repo import OrderRepository
from core.repository.position_repo import PositionRepository
from core.repository.trade_repo import TradeRepository
from core.types import (
    BotMode,
    BotName,
    ExitReason,
    Position,
    PositionStatus,
)
from execution.auto_trader import AutoTradeRouter
from execution.position_manager import PositionManager
from execution.service import TradingService
from execution.trading.subaccount_manager import CoinDCXSubAccountManager


@pytest.fixture
async def exit_test_env():
    mem_uri = f"file:test_exit_{uuid.uuid4().hex}?mode=memory&cache=shared"
    db = Database(path=mem_uri)
    await db.open()

    bus = EventBus()
    pos_repo = PositionRepository(db.connection)
    trade_repo = TradeRepository(db.connection)
    event_repo = EventLogRepository(db.connection)
    order_repo = OrderRepository(db.connection)
    subaccount_mgr = CoinDCXSubAccountManager()

    cfg = AppConfig(
        v2_trading_enabled=True,
        v2_deployment_mode="LIVE_MICROCASH",
        order_size_inr=250.0,
        enforce_single_coin_lock=False,
    )

    trading_svc = TradingService(
        bus=bus,
        position_repo=pos_repo,
        trade_repo=trade_repo,
        event_log_repo=event_repo,
        config=cfg,
        subaccount_manager=subaccount_mgr,
        order_repo=order_repo,
    )

    yield {
        "db": db,
        "bus": bus,
        "pos_repo": pos_repo,
        "trade_repo": trade_repo,
        "event_repo": event_repo,
        "order_repo": order_repo,
        "subaccount_mgr": subaccount_mgr,
        "cfg": cfg,
        "trading_svc": trading_svc,
    }
    await db.close()


# ── Test 1: Disaster stop_limit order created when BUY is filled ──────────────


@pytest.mark.anyio
async def test_disaster_stop_limit_created_on_buy_fill(exit_test_env):
    """When a live BUY order is confirmed FILLED, a resting stop_limit sell order must be placed on CoinDCX."""
    trading_svc = exit_test_env["trading_svc"]
    pos_repo = exit_test_env["pos_repo"]
    mgr = exit_test_env["subaccount_mgr"]
    sub_client = mgr.get_client(BotName.STE)

    # Mock live buy order and resting disaster stop-loss order responses
    sub_client.place_live_order = AsyncMock(
        side_effect=[
            {
                "success": True,
                "exchange_order_id": "EX_BUY_SOL_001",
                "client_order_id": "CL_BUY_SOL_001",
                "status": "FILLED",
                "is_filled": True,
                "filled_qty": 0.02,
                "price": 12500.0,
                "qty": 0.02,
            },
            {
                "success": True,
                "exchange_order_id": "EX_SL_RESTING_999",
                "order": {"id": "EX_SL_RESTING_999"},
            },
        ]
    )

    payload = {
        "signal_id": "sig-sol-001",
        "coin": "SOL",
        "pair": "SOL/INR",
        "bot": "STE",
        "price": 12500.0,
        "approved_amount": 250.0,
        "ai_adjustments": {},
    }

    await trading_svc.on_trade_approved(EventType.TRADE_APPROVED, payload)

    # 1. Verify place_live_order was invoked for disaster stop-loss (2nd call)
    assert sub_client.place_live_order.call_count == 2
    sl_kwargs = sub_client.place_live_order.call_args_list[1][1]
    assert sl_kwargs["side"] == "SELL"
    assert sl_kwargs["qty"] == 0.02
    assert sl_kwargs["stop_price"] is not None
    # Ensure limit price has 0.5% discount buffer below stop_price
    assert sl_kwargs["price"] < sl_kwargs["stop_price"]

    # 2. Verify position in DB has stop_loss_order_id persisted
    open_positions = await pos_repo.get_open()
    assert len(open_positions) == 1
    pos = open_positions[0]
    assert pos.coin == "SOL"
    assert pos.exchange_order_id == "EX_BUY_SOL_001"
    assert pos.stop_loss_order_id == "EX_SL_RESTING_999"


@pytest.mark.anyio
async def test_disaster_stop_limit_failure_emits_alert(exit_test_env):
    """If resting stop loss placement fails on exchange, CRITICAL alert is published."""
    trading_svc = exit_test_env["trading_svc"]
    bus = exit_test_env["bus"]
    mgr = exit_test_env["subaccount_mgr"]
    sub_client = mgr.get_client(BotName.STE)

    sub_client.place_live_order = AsyncMock(
        side_effect=[
            {
                "success": True,
                "exchange_order_id": "EX_BUY_002",
                "status": "FILLED",
                "is_filled": True,
                "filled_qty": 0.02,
                "price": 12500.0,
                "qty": 0.02,
            },
            {
                "success": False,
                "error": "EXCHANGE_UNAVAILABLE",
                "message": "CoinDCX maintenance",
            },
        ]
    )

    alerts = []

    async def on_alert(ev, data):
        alerts.append(data)

    bus.subscribe(EventType.ALERT_GENERATED, on_alert)

    payload = {
        "signal_id": "sig-sol-002",
        "coin": "SOL",
        "pair": "SOL/INR",
        "bot": "STE",
        "price": 12500.0,
        "approved_amount": 250.0,
    }

    await trading_svc.on_trade_approved(EventType.TRADE_APPROVED, payload)

    assert len(alerts) == 1
    assert alerts[0]["level"] == "CRITICAL"
    assert "Disaster Stop-Loss" in alerts[0]["title"]


# ── Test 2: Resting stop order cancelled before TP market sell ────────────────


@pytest.mark.anyio
async def test_resting_stop_loss_cancelled_on_take_profit(exit_test_env):
    """PositionManager cancels resting stop loss on CoinDCX when Take Profit triggers."""
    pos_repo = exit_test_env["pos_repo"]
    trade_repo = exit_test_env["trade_repo"]
    bus = exit_test_env["bus"]
    mgr = exit_test_env["subaccount_mgr"]

    pos_mgr = PositionManager(
        position_repo=pos_repo,
        trade_repo=trade_repo,
        bus=bus,
        subaccount_manager=mgr,
    )

    sub_client = mgr.get_client(BotName.STE)
    sub_client.cancel_order = AsyncMock(return_value={"success": True})

    now = datetime.now(timezone.utc)
    pos = Position(
        id=str(uuid.uuid4()),
        bot=BotName.STE,
        coin="SOL",
        pair="SOL/INR",
        qty=0.02,
        entry_price=12500.0,
        entry_time=now,
        mode=BotMode.LIVE,
        status=PositionStatus.OPEN,
        current_price=12500.0,
        take_profit=13000.0,
        stop_loss=12000.0,
        stop_loss_order_id="EX_RESTING_SL_SOL_01",
    )
    await pos_repo.insert(pos)

    # Current price reaches 13100.0 (triggers TP at 13000.0)
    triggers = await pos_mgr.evaluate_brackets(pair="SOL/INR", current_price=13100.0)

    # 1. Verify cancel_order was awaited with resting stop loss order ID
    assert sub_client.cancel_order.called
    sub_client.cancel_order.assert_awaited_with("EX_RESTING_SL_SOL_01")

    # 2. Verify trigger returned is TAKE_PROFIT
    assert len(triggers) == 1
    trig_pos, reason, exit_px = triggers[0]
    assert reason == ExitReason.TAKE_PROFIT
    assert exit_px == 13100.0

    # 3. Verify SQLite position has stop_loss_order_id cleared
    refreshed_pos = await pos_repo.get_by_id(pos.id)
    assert refreshed_pos.stop_loss_order_id is None


@pytest.mark.anyio
async def test_resting_stop_loss_cancelled_on_trailing_stop(exit_test_env):
    """PositionManager cancels resting stop loss on CoinDCX when Trailing Stop triggers."""
    pos_repo = exit_test_env["pos_repo"]
    trade_repo = exit_test_env["trade_repo"]
    bus = exit_test_env["bus"]
    mgr = exit_test_env["subaccount_mgr"]

    pos_mgr = PositionManager(
        position_repo=pos_repo,
        trade_repo=trade_repo,
        bus=bus,
        subaccount_manager=mgr,
    )

    sub_client = mgr.get_client(BotName.HDA)
    sub_client.cancel_order = AsyncMock(return_value={"success": True})

    pos = Position(
        id=str(uuid.uuid4()),
        bot=BotName.HDA,
        coin="BTC",
        pair="BTC/INR",
        qty=0.001,
        entry_price=8000000.0,
        entry_time=datetime.now(timezone.utc),
        mode=BotMode.LIVE,
        status=PositionStatus.OPEN,
        current_price=8000000.0,
        take_profit=9000000.0,
        stop_loss=7600000.0,
        stop_loss_order_id="EX_RESTING_SL_BTC_02",
    )
    await pos_repo.insert(pos)

    # Price jumps to 8500000 (peak set), trailing stop becomes 8500000 * 0.97 = 8245000
    await pos_mgr.update_mark_price(pos, 8500000.0, trailing_pct=0.03)

    # Price drops to 8200000 (breaches trailing stop 8245000)
    triggers = await pos_mgr.evaluate_brackets(pair="BTC/INR", current_price=8200000.0)

    assert sub_client.cancel_order.called
    sub_client.cancel_order.assert_awaited_with("EX_RESTING_SL_BTC_02")
    assert len(triggers) == 1
    assert triggers[0][1] == ExitReason.STOP_LOSS

    refreshed = await pos_repo.get_by_id(pos.id)
    assert refreshed.stop_loss_order_id is None


# ── Test 3: Strict manual close verification gate ────────────────────────────


@pytest.mark.anyio
async def test_manual_close_reverts_to_open_when_dispatch_fails(exit_test_env):
    """When manual SELL order dispatch fails, position reverts to OPEN with CRITICAL alert."""
    trading_svc = exit_test_env["trading_svc"]
    pos_repo = exit_test_env["pos_repo"]
    bus = exit_test_env["bus"]
    mgr = exit_test_env["subaccount_mgr"]
    sub_client = mgr.get_client(BotName.STE)

    pos = Position(
        id=str(uuid.uuid4()),
        bot=BotName.STE,
        coin="SOL",
        pair="SOL/INR",
        qty=0.02,
        entry_price=12500.0,
        entry_time=datetime.now(timezone.utc),
        mode=BotMode.LIVE,
        status=PositionStatus.OPEN,
        current_price=12500.0,
        stop_loss_order_id="SL_RESTING_123",
    )
    await pos_repo.insert(pos)

    sub_client.cancel_order = AsyncMock(return_value={"success": True})
    sub_client.place_live_order = AsyncMock(
        return_value={
            "success": False,
            "error": "INSUFFICIENT_LIQUIDITY",
            "message": "Market order rejected: insufficient order book depth",
        }
    )

    alerts = []

    async def on_alert(ev, data):
        alerts.append(data)

    bus.subscribe(EventType.ALERT_GENERATED, on_alert)

    res = await trading_svc.manual_close_position(pos.id)

    assert res["success"] is False
    assert res["error"] == "EXCHANGE_ORDER_FAILED"

    # Verify status reverted to OPEN in SQLite
    reverted_pos = await pos_repo.get_by_id(pos.id)
    assert reverted_pos.status == PositionStatus.OPEN

    # Verify alert was published
    assert len(alerts) == 1
    assert alerts[0]["level"] == "CRITICAL"
    assert "Manual Close Dispatch Failed" in alerts[0]["title"]


@pytest.mark.anyio
async def test_manual_close_polls_until_filled(exit_test_env):
    """When manual SELL order is initially OPEN, verification gate polls status up to 5 times until FILLED."""
    trading_svc = exit_test_env["trading_svc"]
    pos_repo = exit_test_env["pos_repo"]
    mgr = exit_test_env["subaccount_mgr"]
    sub_client = mgr.get_client(BotName.STE)

    pos = Position(
        id=str(uuid.uuid4()),
        bot=BotName.STE,
        coin="SOL",
        pair="SOL/INR",
        qty=0.02,
        entry_price=12500.0,
        entry_time=datetime.now(timezone.utc),
        mode=BotMode.LIVE,
        status=PositionStatus.OPEN,
        current_price=12500.0,
    )
    await pos_repo.insert(pos)

    sub_client.cancel_order = AsyncMock(return_value={"success": True})
    sub_client.place_live_order = AsyncMock(
        return_value={
            "success": True,
            "exchange_order_id": "EX_SELL_POLL_01",
            "status": "OPEN",
            "is_filled": False,
            "filled_qty": 0.0,
        }
    )

    # Poll 1: OPEN, Poll 2: FILLED
    poll_results = [
        {"success": True, "status": "OPEN", "is_filled": False, "filled_qty": 0.0},
        {"success": True, "status": "FILLED", "is_filled": True, "filled_qty": 0.02},
    ]
    sub_client.get_order_status = AsyncMock(side_effect=poll_results)

    with patch("asyncio.sleep", new_callable=AsyncMock):
        res = await trading_svc.manual_close_position(pos.id, exit_price=12600.0)

    assert res["success"] is True
    assert sub_client.get_order_status.call_count == 2

    # Verify closed in SQLite
    closed_pos = await pos_repo.get_by_id(pos.id)
    assert closed_pos.status == PositionStatus.CLOSED


@pytest.mark.anyio
async def test_manual_close_reverts_to_open_on_poll_timeout(exit_test_env):
    """When manual SELL order remains unconfirmed after 5 status polls, position safely reverts to OPEN."""
    trading_svc = exit_test_env["trading_svc"]
    pos_repo = exit_test_env["pos_repo"]
    bus = exit_test_env["bus"]
    mgr = exit_test_env["subaccount_mgr"]
    sub_client = mgr.get_client(BotName.STE)

    pos = Position(
        id=str(uuid.uuid4()),
        bot=BotName.STE,
        coin="SOL",
        pair="SOL/INR",
        qty=0.02,
        entry_price=12500.0,
        entry_time=datetime.now(timezone.utc),
        mode=BotMode.LIVE,
        status=PositionStatus.OPEN,
        current_price=12500.0,
    )
    await pos_repo.insert(pos)

    sub_client.cancel_order = AsyncMock(return_value={"success": True})
    sub_client.place_live_order = AsyncMock(
        return_value={
            "success": True,
            "exchange_order_id": "EX_SELL_STUCK_01",
            "status": "OPEN",
            "is_filled": False,
            "filled_qty": 0.0,
        }
    )

    sub_client.get_order_status = AsyncMock(
        return_value={
            "success": True,
            "status": "OPEN",
            "is_filled": False,
            "filled_qty": 0.0,
        }
    )

    alerts = []

    async def on_alert(ev, data):
        alerts.append(data)

    bus.subscribe(EventType.ALERT_GENERATED, on_alert)

    with patch("asyncio.sleep", new_callable=AsyncMock):
        res = await trading_svc.manual_close_position(pos.id)

    assert res["success"] is False
    assert res["error"] == "ORDER_UNFILLED"
    assert sub_client.get_order_status.call_count == 5

    # Safe state invariant: position must NOT be closed
    reverted_pos = await pos_repo.get_by_id(pos.id)
    assert reverted_pos.status == PositionStatus.OPEN

    assert len(alerts) == 1
    assert alerts[0]["level"] == "CRITICAL"
    assert "Manual Close Fill Unconfirmed" in alerts[0]["title"]


# ── Test 4: Pre-trade slippage and ticker age guards ──────────────────────────


@pytest.mark.anyio
async def test_auto_trader_rejects_stale_ticker_data():
    """AutoTradeRouter rejects signals where ticker data age > 5.0 seconds."""
    bus = EventBus()
    router = AutoTradeRouter(bus=bus, dry_run=True)

    # Ticker timestamp 10 seconds in the past
    stale_payload = {
        "id": "sig-stale-001",
        "coin": "SOL",
        "pair": "SOL/INR",
        "price": 12500.0,
        "trade_amount": 250.0,
        "ticker_timestamp": time.time() - 10.0,
    }

    res = await router.handle_signal(stale_payload)
    assert res is not None
    assert res["success"] is False
    assert res["error"] == "STALE_MARKET_DATA"
    assert "exceeds maximum allowable 5.0s limit" in res["message"]


@pytest.mark.anyio
async def test_auto_trader_rejects_excessive_slippage():
    """AutoTradeRouter rejects signals where execution slippage exceeds 0.50%."""
    bus = EventBus()
    router = AutoTradeRouter(bus=bus, dry_run=True)

    # Signal price is 10000.0, current ticker price is 10100.0 (1.0% slippage > 0.50%)
    slippage_payload = {
        "id": "sig-slip-001",
        "coin": "SOL",
        "pair": "SOL/INR",
        "signal_price": 10000.0,
        "current_price": 10100.0,
        "trade_amount": 250.0,
        "ticker_timestamp": time.time(),
    }

    res = await router.handle_signal(slippage_payload)
    assert res is not None
    assert res["success"] is False
    assert res["error"] == "SLIPPAGE_EXCEEDED"
    assert "exceeds maximum allowable 0.50%" in res["message"]


@pytest.mark.anyio
async def test_auto_trader_accepts_valid_signal_within_tolerances():
    """AutoTradeRouter accepts signals with fresh ticker (<5s) and slippage <= 0.50%."""
    bus = EventBus()
    router = AutoTradeRouter(bus=bus, dry_run=True)

    # Slippage is 0.20% (<= 0.50%), age is 1.0s (<= 5.0s)
    valid_payload = {
        "id": "sig-valid-001",
        "coin": "SOL",
        "pair": "SOL/INR",
        "signal_price": 10000.0,
        "current_price": 10020.0,
        "trade_amount": 250.0,
        "ticker_timestamp": time.time() - 1.0,
    }

    res = await router.handle_signal(valid_payload)
    assert res is not None
    assert res["success"] is True
    assert res["dry_run"] is True
