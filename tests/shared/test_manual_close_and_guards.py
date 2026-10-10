"""
V2 Manual Live Position Close, Execution Safety Guards, and Production Security Test Suite (P0-03, P0-04, P0-05).
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from core.bus.event_bus import EventBus
from core.config import AppConfig
from core.exceptions import SecurityConfigError
from core.repository.db import Database
from core.repository.event_log_repo import EventLogRepository
from core.repository.order_repo import OrderRepository
from core.repository.position_repo import PositionRepository
from core.repository.trade_repo import TradeRepository
from core.types import BotMode, BotName, Position, PositionStatus
from execution.service import TradingService
from execution.trading.execution_guards import ExecutionSafetyGuards
from execution.trading.subaccount_manager import CoinDCXSubAccountManager


@pytest.fixture
async def db_env():
    mem_uri = f"file:test_mc_guards_{uuid.uuid4().hex}?mode=memory&cache=shared"
    db = Database(path=mem_uri)
    await db.open()

    order_repo = OrderRepository(db.connection)
    pos_repo = PositionRepository(db.connection)
    trade_repo = TradeRepository(db.connection)
    event_log_repo = EventLogRepository(db.connection)

    yield {
        "db": db,
        "order_repo": order_repo,
        "pos_repo": pos_repo,
        "trade_repo": trade_repo,
        "event_log_repo": event_log_repo,
    }
    await db.close()


# ── P0-03: Manual Live Position Close Tests ───────────────────────────────────


@pytest.mark.anyio
async def test_manual_close_full_fill_closes_position(db_env):
    """P0-03: Confirmed full fill on manual SELL closes position record in SQLite."""
    pos_repo = db_env["pos_repo"]
    trade_repo = db_env["trade_repo"]
    order_repo = db_env["order_repo"]
    event_log_repo = db_env["event_log_repo"]

    pos = Position(
        id="POS-MC-1",
        bot=BotName.STE,
        coin="ETH",
        pair="ETH/INR",
        qty=1.0,
        entry_price=300000.0,
        entry_time=datetime.now(timezone.utc),
        mode=BotMode.LIVE,
        status=PositionStatus.OPEN,
        exchange_order_id="EX-BUY-1",
    )
    await pos_repo.insert(pos)

    bus = EventBus()
    cfg = AppConfig(v2_trading_enabled=True, v2_deployment_mode="LIVE_MICROCASH")

    mgr = CoinDCXSubAccountManager()
    client = mgr.get_client(BotName.STE)
    client.place_live_order = AsyncMock(
        return_value={
            "success": True,
            "exchange_order_id": "EX-SELL-1",
            "status": "FILLED",
            "is_filled": True,
            "filled_qty": 1.0,
            "price": 310000.0,
        }
    )

    service = TradingService(
        bus=bus,
        position_repo=pos_repo,
        trade_repo=trade_repo,
        event_log_repo=event_log_repo,
        config=cfg,
        subaccount_manager=mgr,
        order_repo=order_repo,
    )

    res = await service.manual_close_position("POS-MC-1", exit_price=310000.0)
    assert res["success"] is True
    assert res["status"] == "CLOSED"

    db_pos = await pos_repo.get_by_id("POS-MC-1")
    assert db_pos.status == PositionStatus.CLOSED


@pytest.mark.anyio
async def test_manual_close_partial_fill_keeps_position_open(db_env):
    """P0-03: Partial fill on manual SELL updates remaining quantity and leaves position OPEN."""
    pos_repo = db_env["pos_repo"]
    trade_repo = db_env["trade_repo"]
    order_repo = db_env["order_repo"]
    event_log_repo = db_env["event_log_repo"]

    pos = Position(
        id="POS-MC-2",
        bot=BotName.STE,
        coin="SOL",
        pair="SOL/INR",
        qty=10.0,
        entry_price=15000.0,
        entry_time=datetime.now(timezone.utc),
        mode=BotMode.LIVE,
        status=PositionStatus.OPEN,
    )
    await pos_repo.insert(pos)

    bus = EventBus()
    cfg = AppConfig(v2_trading_enabled=True, v2_deployment_mode="LIVE_MICROCASH")

    mgr = CoinDCXSubAccountManager()
    client = mgr.get_client(BotName.STE)
    client.place_live_order = AsyncMock(
        return_value={
            "success": True,
            "exchange_order_id": "EX-SELL-2",
            "status": "PARTIALLY_FILLED",
            "is_filled": False,
            "filled_qty": 4.0,
            "price": 15500.0,
        }
    )

    service = TradingService(
        bus=bus,
        position_repo=pos_repo,
        trade_repo=trade_repo,
        event_log_repo=event_log_repo,
        config=cfg,
        subaccount_manager=mgr,
        order_repo=order_repo,
    )

    res = await service.manual_close_position("POS-MC-2", exit_price=15500.0)
    assert res["success"] is True
    assert res["status"] == "PARTIALLY_FILLED"
    assert res["filled_qty"] == 4.0
    assert res["remaining_qty"] == 6.0

    db_pos = await pos_repo.get_by_id("POS-MC-2")
    assert db_pos.status == PositionStatus.OPEN
    assert db_pos.qty == 6.0


@pytest.mark.anyio
async def test_manual_close_exchange_rejection_keeps_position_open(db_env):
    """P0-03: Exchange rejection or failure on manual SELL does NOT close local position."""
    pos_repo = db_env["pos_repo"]
    trade_repo = db_env["trade_repo"]
    order_repo = db_env["order_repo"]
    event_log_repo = db_env["event_log_repo"]

    pos = Position(
        id="POS-MC-3",
        bot=BotName.STE,
        coin="BTC",
        pair="BTC/INR",
        qty=0.01,
        entry_price=7000000.0,
        entry_time=datetime.now(timezone.utc),
        mode=BotMode.LIVE,
        status=PositionStatus.OPEN,
    )
    await pos_repo.insert(pos)

    bus = EventBus()
    cfg = AppConfig(v2_trading_enabled=True, v2_deployment_mode="LIVE_MICROCASH")

    mgr = CoinDCXSubAccountManager()
    client = mgr.get_client(BotName.STE)
    client.place_live_order = AsyncMock(
        return_value={
            "success": False,
            "error": "INSUFFICIENT_BALANCE",
            "message": "Exchange balance insufficient for sell order",
        }
    )

    service = TradingService(
        bus=bus,
        position_repo=pos_repo,
        trade_repo=trade_repo,
        event_log_repo=event_log_repo,
        config=cfg,
        subaccount_manager=mgr,
        order_repo=order_repo,
    )

    res = await service.manual_close_position("POS-MC-3", exit_price=7100000.0)
    assert res["success"] is False
    assert res["error"] == "EXCHANGE_ORDER_FAILED"

    db_pos = await pos_repo.get_by_id("POS-MC-3")
    assert db_pos.status == PositionStatus.OPEN
    assert db_pos.qty == 0.01


# ── P0-04: LIVE Execution Safety Guards Tests ────────────────────────────────


def test_stale_data_guard_normal_vs_emergency_exit():
    """P0-04: Stale market data guard rejects old data for normal orders but passes for emergency exits."""
    guards = ExecutionSafetyGuards(max_stale_seconds=60.0)

    old_ts = datetime.now(timezone.utc).timestamp() - 120.0  # 120s old

    # Normal order check -> Rejected
    normal_res = guards.check_stale_data_guard(old_ts, is_emergency_exit=False)
    assert normal_res.passed is False
    assert normal_res.code == "STALE_DATA_REJECTED"

    # Emergency exit check -> Bypassed / Passed
    emg_res = guards.check_stale_data_guard(old_ts, is_emergency_exit=True)
    assert emg_res.passed is True
    assert emg_res.code == "BYPASSED_EMERGENCY"


def test_slippage_guard_normal_vs_emergency_exit():
    """P0-04: Slippage guard rejects high price deviation for normal orders but passes for emergency exits."""
    guards = ExecutionSafetyGuards(max_slippage_pct=3.0)

    # 5.0% price deviation
    order_px = 105.0
    ticker_px = 100.0

    normal_res = guards.check_slippage_guard(
        order_px, ticker_px, is_emergency_exit=False
    )
    assert normal_res.passed is False
    assert normal_res.code == "SLIPPAGE_REJECTED"

    emg_res = guards.check_slippage_guard(order_px, ticker_px, is_emergency_exit=True)
    assert emg_res.passed is True
    assert emg_res.code == "BYPASSED_EMERGENCY"


def test_liquidity_and_duplicate_order_guards():
    """P0-04: Liquidity guard and Duplicate Order guard enforcement."""
    guards = ExecutionSafetyGuards(min_24h_volume=50000.0)

    # Low volume coin -> Rejected
    liq_res = guards.check_liquidity_guard("RARE/INR", volume_24h=12000.0)
    assert liq_res.passed is False
    assert liq_res.code == "INSUFFICIENT_LIQUIDITY"

    # High volume coin -> Passed
    liq_pass = guards.check_liquidity_guard("BTC/INR", volume_24h=5000000.0)
    assert liq_pass.passed is True

    # Active coin duplicate order -> Rejected
    dup_res = guards.check_duplicate_order_guard(
        "SOL", active_coins=["SOL/INR", "BTC/INR"]
    )
    assert dup_res.passed is False
    assert dup_res.code == "DUPLICATE_POSITION_EXISTS"

    # Non-duplicate coin -> Passed
    dup_pass = guards.check_duplicate_order_guard(
        "ETH", active_coins=["SOL/INR", "BTC/INR"]
    )
    assert dup_pass.passed is True


# ── P0-05: Production Security Hardening Tests ───────────────────────────────


def test_validate_live_security_fails_fast_on_dummy_credentials():
    """P0-05: validate_live_security raises SecurityConfigError when LIVE mode is active with dummy credentials."""
    cfg = AppConfig()
    cfg.v2_deployment_mode = "LIVE_MICROCASH"
    cfg.v2_trading_enabled = True
    cfg.coindcx_live_api_key = "DUMMY_KEY"
    cfg.coindcx_live_api_secret = "sample_secret"
    cfg.operator_security_password = None

    with pytest.raises(SecurityConfigError):
        cfg.validate_live_security()


def test_config_secret_redaction():
    """P0-05: get_sanitized_config_dict redacts secret fields for safe logging."""
    cfg = AppConfig()
    cfg.coindcx_api_secret = "super_secret_123"
    cfg.alert_bot_token = "bot_token_abc"
    cfg.operator_security_password = "password_123"

    sanitized = cfg.get_sanitized_config_dict()
    assert sanitized["coindcx_api_secret"] == "***REDACTED***"
    assert sanitized["alert_bot_token"] == "***REDACTED***"
    assert sanitized["operator_security_password"] == "***REDACTED***"


@pytest.mark.anyio
async def test_manual_close_zero_fill_keeps_position_open(db_env):
    """P0-03: Zero fill on manual SELL order keeps position OPEN and returns ORDER_UNFILLED error."""
    pos_repo = db_env["pos_repo"]
    trade_repo = db_env["trade_repo"]
    order_repo = db_env["order_repo"]
    event_log_repo = db_env["event_log_repo"]

    pos = Position(
        id="POS-MC-4",
        bot=BotName.STE,
        coin="AVAX",
        pair="AVAX/INR",
        qty=5.0,
        entry_price=2000.0,
        entry_time=datetime.now(timezone.utc),
        mode=BotMode.LIVE,
        status=PositionStatus.OPEN,
    )
    await pos_repo.insert(pos)

    bus = EventBus()
    cfg = AppConfig(v2_trading_enabled=True, v2_deployment_mode="LIVE_MICROCASH")

    mgr = CoinDCXSubAccountManager()
    client = mgr.get_client(BotName.STE)
    client.place_live_order = AsyncMock(
        return_value={
            "success": True,
            "exchange_order_id": "EX-SELL-4",
            "status": "OPEN",
            "is_filled": False,
            "filled_qty": 0.0,
            "price": 2100.0,
        }
    )

    service = TradingService(
        bus=bus,
        position_repo=pos_repo,
        trade_repo=trade_repo,
        event_log_repo=event_log_repo,
        config=cfg,
        subaccount_manager=mgr,
        order_repo=order_repo,
    )

    res = await service.manual_close_position("POS-MC-4", exit_price=2100.0)
    assert res["success"] is False
    assert res["error"] == "ORDER_UNFILLED"

    db_pos = await pos_repo.get_by_id("POS-MC-4")
    assert db_pos.status == PositionStatus.OPEN
    assert db_pos.qty == 5.0


@pytest.mark.anyio
async def test_trading_service_enforces_execution_guards(db_env):
    """P0-04: TradingService blocks live order placement when ExecutionSafetyGuards fail."""
    pos_repo = db_env["pos_repo"]
    trade_repo = db_env["trade_repo"]
    order_repo = db_env["order_repo"]
    event_log_repo = db_env["event_log_repo"]

    bus = EventBus()
    cfg = AppConfig(
        v2_trading_enabled=True,
        v2_deployment_mode="LIVE_MICROCASH",
        scanner_min_24h_volume=50000.0,
    )

    mgr = CoinDCXSubAccountManager()
    client = mgr.get_client(BotName.STE)
    client.place_live_order = AsyncMock()

    service = TradingService(
        bus=bus,
        position_repo=pos_repo,
        trade_repo=trade_repo,
        event_log_repo=event_log_repo,
        config=cfg,
        subaccount_manager=mgr,
        order_repo=order_repo,
    )

    # Signal payload with 10s old timestamp (exceeds max_stale_seconds 60s) but low volume
    stale_ts = datetime.now(timezone.utc).timestamp() - 120.0
    payload = {
        "bot": "STE",
        "coin": "ILLIQUID",
        "pair": "ILLIQUID/INR",
        "price": 100.0,
        "entry_price": 100.0,
        "amount": 200.0,
        "qty": 2.0,
        "stop_loss": 95.0,
        "take_profit": 110.0,
        "timestamp": stale_ts,
        "volume_24h": 1000.0,  # Below min 50,000 threshold
        "signal_id": "SIG-GUARD-1",
    }

    from core.bus.event_types import EventType

    await service.on_trade_approved(EventType.TRADE_APPROVED, payload)
    client.place_live_order.assert_not_called()
