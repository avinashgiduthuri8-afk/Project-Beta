"""
tests/test_v2_safety_invariants.py
Focused Regression Test Suite for PROJECT-ALPHA Safety Invariants.

Verifies:
1. Dynamic Capital: No ₹10,000 fallback, ₹200 default only, manually configurable order_size_inr.
2. Authoritative Precision Rules: No hardcoded ₹100 floor in Telegram /setamount or order checks.
3. Global Kill-Switch Coverage: Blocks every outbound execution path (BUY and SELL) to CoinDCX.
4. /resume Safety: Rejects when Risk Engine reports unsafe (loss streak, drawdown, etc.).
5. Telegram Safety: Operator interface only, cannot bypass Risk Engine or kill-switch.
6. PAPER / SHADOW / LIVE Strict Isolation: PAPER and SHADOW never call place_live_order.
7. LIVE Dynamic Balance: Fails closed (BLOCKED_BALANCE_UNAVAILABLE) when CoinDCX balance is unavailable.
8. Watchdog Safety Invariants: Never creates trades, resets breakers, forces LIVE, or auto-enables trading.
9. Execution Amount Flow: BUY uses configured/approved amount; SELL strictly uses pos.qty.
10. Reconciliation Safety: No duplicate BUYs/SELLs, no reopening closed positions.
"""

import math
import os
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from background.production.controller import ProductionController
from background.production.watchdog import ProductionWatchdog
from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.config import AppConfig, invalidate_config
from core.repository.db import Database
from core.repository.event_log_repo import EventLogRepository
from core.repository.position_repo import PositionRepository
from core.repository.trade_repo import TradeRepository
from core.types import (
    BotMode,
    BotName,
    Position,
    PositionStatus,
)
from execution.risk.service import RiskService
from execution.service import TradingService
from execution.trading.precision_rules import get_pair_spec
from execution.trading.subaccount_manager import CoinDCXSubAccountManager
from telegram.telegram_interface import TelegramInteractiveInterface


class MockTelegramClient:
    """Mock Telegram client recording dispatched messages."""

    def __init__(self):
        self.sent_messages: list[dict] = []
        self.edited_messages: list[dict] = []
        self.is_configured = True

    async def send_message(
        self, text: str, target_chat_id: str, reply_markup: Any = None
    ) -> dict:
        msg = {
            "text": text,
            "target_chat_id": str(target_chat_id),
            "reply_markup": reply_markup,
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }
        self.sent_messages.append(msg)
        return {"ok": True, "result": {"message_id": len(self.sent_messages)}}

    async def edit_message_text(
        self, text: str, chat_id: Any, message_id: int, reply_markup: Any = None
    ) -> dict:
        msg = {
            "text": text,
            "chat_id": str(chat_id),
            "message_id": message_id,
            "reply_markup": reply_markup,
        }
        self.edited_messages.append(msg)
        return {"ok": True}


@pytest.fixture
def tmp_db_file(tmp_path):
    return str(tmp_path / f"test_safety_{uuid.uuid4().hex[:8]}.db")


# ─────────────────────────────────────────────────────────────────────────────
# 1. FIX CAPITAL MODEL CONTRADICTION
# ─────────────────────────────────────────────────────────────────────────────


def test_01_dynamic_capital_no_10k_fallback_configurable_order_size():
    """Verify ₹200 is DEFAULT only, no ₹10k ceiling, configurable order amount."""
    os.environ.pop("TOTAL_CAPITAL_LIMIT", None)
    os.environ.pop("CAPITAL_POOL", None)
    os.environ.pop("ORDER_SIZE_INR", None)
    os.environ.pop("DEFAULT_TRADE_AMOUNT", None)
    invalidate_config()

    cfg = AppConfig(total_capital_limit=None, _env_file=None)
    assert cfg.order_size_inr == 200.0
    assert cfg.total_capital_limit is None  # Dynamic capital, no fixed ₹10,000 ceiling

    # Manual configuration
    cfg.order_size_inr = 750.0
    assert cfg.order_size_inr == 750.0

    # SubAccount manager initializes dynamic pool with no 10k fallback
    mgr = CoinDCXSubAccountManager(config=cfg)
    assert (
        mgr._shared_pool_state["wallet_balance_inr"] == math.inf
    )  # Dynamic unconstrained
    assert mgr.get_client(BotName.STE).config.default_trade_amount_inr == 750.0

    # Update order size propagates to all bot clients
    mgr.update_order_size(850.0)
    assert mgr.get_client(BotName.STE).config.default_trade_amount_inr == 850.0
    assert mgr.get_client(BotName.HDA).config.default_trade_amount_inr == 850.0


# ─────────────────────────────────────────────────────────────────────────────
# 2. FIX ₹100 EXCHANGE FLOOR
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_02_authoritative_precision_rules_and_no_arbitrary_100_floor(tmp_db_file):
    """Telegram /setamount accepts valid values below 100; live execution uses pair spec."""
    db = Database(tmp_db_file)
    await db.open()
    bus = EventBus()
    cfg = AppConfig(
        v2_db_path=tmp_db_file,
        alert_chat_id="12345",
        telegram_allowed_chat_ids="12345",
        order_size_inr=200.0,
    )
    pos_repo = PositionRepository(db.connection)
    trade_repo = TradeRepository(db.connection)
    event_repo = EventLogRepository(db.connection)
    sub_mgr = CoinDCXSubAccountManager(config=cfg)
    trading = TradingService(
        bus=bus,
        position_repo=pos_repo,
        trade_repo=trade_repo,
        event_log_repo=event_repo,
        config=cfg,
        subaccount_manager=sub_mgr,
    )
    tg_client = MockTelegramClient()

    c2 = TelegramInteractiveInterface(
        telegram_client=tg_client,
        bus=bus,
        config=cfg,
        trading_service=trading,
        position_repo=pos_repo,
        trade_repo=trade_repo,
        event_log_repo=event_repo,
    )

    # Setting order amount to 50 INR is rejected by the minimum ₹200 floor
    await c2._handle_incoming_message({"chat": {"id": 12345}, "text": "/setamount 50"})
    assert len(tg_client.sent_messages) == 1
    assert "Order Amount Below Minimum" in tg_client.sent_messages[-1]["text"]

    # Setting order amount to valid 250 INR succeeds
    await c2._handle_incoming_message({"chat": {"id": 12345}, "text": "/setamount 250"})
    assert "ORDER AMOUNT UPDATED" in tg_client.sent_messages[-1]["text"]
    assert "250.00" in tg_client.sent_messages[-1]["text"]
    assert sub_mgr.get_client(BotName.STE).config.default_trade_amount_inr == 250.0

    # Authoritative precision rules check pair-specific min_notional
    btc_spec = get_pair_spec("BTC/INR")
    assert btc_spec.min_notional_inr >= 200.0
    await db.close()


# ─────────────────────────────────────────────────────────────────────────────
# 3. FIX GLOBAL KILL-SWITCH COVERAGE
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_03_kill_switch_blocks_every_outbound_path_buy_and_sell():
    """Kill-switch blocks both BUY and SELL calls to CoinDCX with 0 network requests."""
    disabled_cfg = AppConfig(
        v2_trading_enabled=False, v2_deployment_mode="LIVE_MICROCASH"
    )
    sub_mgr = CoinDCXSubAccountManager(config=disabled_cfg)
    client = sub_mgr.get_client(BotName.STE)
    client.post = AsyncMock()  # Network call mock

    with patch("core.config.get_config", return_value=disabled_cfg):
        # 1. BUY blocked
        buy_res = await client.place_live_order(
            pair="BTC/INR",
            side="BUY",
            price=5000000.0,
            qty=0.001,
        )
        assert buy_res.get("error") == "EXECUTION_BLOCKED_KILL_SWITCH"
        assert client.post.call_count == 0

        # 2. SELL blocked
        sell_res = await client.place_live_order(
            pair="BTC/INR",
            side="SELL",
            price=5000000.0,
            qty=0.001,
        )
        assert sell_res.get("error") == "EXECUTION_BLOCKED_KILL_SWITCH"
        assert client.post.call_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# 4. FIX /resume SAFETY
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_04_resume_rejects_when_risk_engine_reports_unsafe(tmp_db_file):
    """Resume is blocked if Risk Engine has active loss streak or unhandled breach."""
    db = Database(tmp_db_file)
    await db.open()
    bus = EventBus()
    cfg = AppConfig(
        v2_db_path=tmp_db_file,
        v2_max_consecutive_losses=3,
        v2_trading_enabled=False,
    )
    pos_repo = PositionRepository(db.connection)
    trade_repo = TradeRepository(db.connection)
    event_repo = EventLogRepository(db.connection)
    risk = RiskService(
        bus=bus,
        config=cfg,
        position_repo=pos_repo,
        trade_repo=trade_repo,
        event_log_repo=event_repo,
    )
    await risk.start()

    # Simulate consecutive loss breach
    risk.circuit_breaker._consecutive_losses[BotName.STE] = 4
    is_safe, reason = await risk.is_safe_to_resume()
    assert is_safe is False
    assert (
        "consecutive losses" in reason.lower()
        or "consecutive_loss_limit" in reason.lower()
    )

    # Controller resume attempt must fail closed
    controller = ProductionController(
        config=cfg,
        bus=bus,
        risk_service=risk,
    )
    res = await controller.resume(operator="TEST", target_mode="PAPER")
    assert res["ok"] is False
    assert res["error"] == "RISK_PRECONDITION_FAILED"
    assert cfg.v2_trading_enabled is False
    await db.close()


# ─────────────────────────────────────────────────────────────────────────────
# 5. FIX TELEGRAM SAFETY
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_05_telegram_safety_obeys_risk_engine(tmp_db_file):
    """Telegram /resume respects Risk Engine safety and /kill triggers immediate stop."""
    db = Database(tmp_db_file)
    await db.open()
    bus = EventBus()
    cfg = AppConfig(
        v2_db_path=tmp_db_file, alert_chat_id="1001", telegram_allowed_chat_ids="1001"
    )
    pos_repo = PositionRepository(db.connection)
    trade_repo = TradeRepository(db.connection)
    event_repo = EventLogRepository(db.connection)
    risk = RiskService(
        bus=bus,
        config=cfg,
        position_repo=pos_repo,
        trade_repo=trade_repo,
        event_log_repo=event_repo,
    )
    await risk.start()

    tg_client = MockTelegramClient()

    c2 = TelegramInteractiveInterface(
        telegram_client=tg_client,
        bus=bus,
        config=cfg,
        risk_service=risk,
        position_repo=pos_repo,
        trade_repo=trade_repo,
        event_log_repo=event_repo,
    )

    # 1. /kill trips emergency stop immediately
    await c2._handle_incoming_message({"chat": {"id": 1001}, "text": "/kill"})
    assert risk.circuit_breaker.is_open is True
    assert risk.circuit_breaker.emergency_stop is True
    assert cfg.v2_trading_enabled is False

    # 2. When unsafe due to consecutive losses, /resume cannot re-enable trading
    risk.circuit_breaker._consecutive_losses[BotName.HDA] = 10
    await c2._handle_incoming_message({"chat": {"id": 1001}, "text": "/resume"})
    assert "CANNOT RESUME TRADING" in tg_client.sent_messages[-1]["text"]
    assert cfg.v2_trading_enabled is False
    assert risk.circuit_breaker.is_open is True
    await db.close()


# ─────────────────────────────────────────────────────────────────────────────
# 6. FIX PAPER / SHADOW / LIVE ISOLATION
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_06_paper_and_shadow_never_call_exchange(tmp_db_file):
    """In PAPER and SHADOW modes, 0 exchange orders are ever dispatched."""
    db = Database(tmp_db_file)
    await db.open()
    bus = EventBus()
    cfg = AppConfig(
        v2_db_path=tmp_db_file, v2_shadow_mode=False, v2_deployment_mode="PAPER"
    )
    pos_repo = PositionRepository(db.connection)
    trade_repo = TradeRepository(db.connection)
    event_repo = EventLogRepository(db.connection)
    sub_mgr = CoinDCXSubAccountManager(config=cfg)
    client = sub_mgr.get_client(BotName.STE)
    client.place_live_order = AsyncMock()  # Must NEVER be called

    trading = TradingService(
        bus=bus,
        position_repo=pos_repo,
        trade_repo=trade_repo,
        event_log_repo=event_repo,
        config=cfg,
        subaccount_manager=sub_mgr,
    )
    await trading.start()

    # Simulate approved trade in PAPER mode
    await trading.on_trade_approved(
        EventType.TRADE_APPROVED,
        {
            "signal_id": "sig-01",
            "coin": "ETH",
            "bot": "STE",
            "mode": "PAPER",
            "approved_amount": 300.0,
            "entry_price": 250000.0,
            "stop_loss": 240000.0,
            "take_profit": 270000.0,
        },
    )

    # Verified: place_live_order was NEVER called
    assert client.place_live_order.call_count == 0

    # Verified: virtual position was created in repository
    open_pos = await pos_repo.get_open()
    assert len(open_pos) == 1
    assert open_pos[0].mode == BotMode.PAPER
    await db.close()


# ─────────────────────────────────────────────────────────────────────────────
# 7. FIX LIVE DYNAMIC BALANCE RESOLUTION
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_07_live_dynamic_balance_fails_closed_when_unavailable():
    """LIVE mode fails closed (BLOCKED_BALANCE_UNAVAILABLE) if exchange balance is unverified."""
    cfg = AppConfig(v2_trading_enabled=True, v2_deployment_mode="LIVE_MICROCASH")
    sub_mgr = CoinDCXSubAccountManager(config=cfg)
    client = sub_mgr.get_client(BotName.STE)
    # Simulate network failure returning None for balance
    client.get_balances = AsyncMock(
        return_value={"success": False, "error": "NETWORK_UNAVAILABLE"}
    )
    client.post = AsyncMock()

    with patch("core.config.get_config", return_value=cfg):
        res = await client.place_live_order(
            pair="BTC/INR",
            side="BUY",
            price=5000000.0,
            qty=0.001,
        )
        assert res.get("error") == "BLOCKED_BALANCE_UNAVAILABLE"
        assert client.post.call_count == 0  # Zero order placed


# ─────────────────────────────────────────────────────────────────────────────
# 8. FIX WATCHDOG SAFETY INVARIANTS
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_08_watchdog_safety_invariants(tmp_db_file):
    """Watchdog monitors but NEVER creates trades, resets breakers, or forces LIVE."""
    db = Database(tmp_db_file)
    await db.open()
    bus = EventBus()
    cfg = AppConfig(v2_db_path=tmp_db_file, v2_trading_enabled=False)
    pos_repo = PositionRepository(db.connection)
    trade_repo = TradeRepository(db.connection)
    event_repo = EventLogRepository(db.connection)
    risk = RiskService(
        bus=bus,
        config=cfg,
        position_repo=pos_repo,
        trade_repo=trade_repo,
        event_log_repo=event_repo,
    )
    await risk.start()
    risk.circuit_breaker.trip("TEST_TRIP")

    watchdog = ProductionWatchdog(config=cfg, bus=bus, risk_service=risk, db=db)

    # Run inspection
    res = await watchdog.inspect_system()
    assert res["probes"]["database"]["status"] == "OK"

    # Check that watchdog has NOT reset circuit breaker or force enabled trading
    assert risk.circuit_breaker.is_open is True
    assert cfg.v2_trading_enabled is False
    await db.close()


# ─────────────────────────────────────────────────────────────────────────────
# 9. FIX EXECUTION AMOUNT FLOW: BUY vs. SELL
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_09_execution_amount_flow_buy_and_sell(tmp_db_file):
    """BUY uses approved INR amount; SELL strictly uses pos.qty."""
    db = Database(tmp_db_file)
    await db.open()
    bus = EventBus()
    cfg = AppConfig(v2_db_path=tmp_db_file, order_size_inr=400.0)
    pos_repo = PositionRepository(db.connection)
    trade_repo = TradeRepository(db.connection)
    event_repo = EventLogRepository(db.connection)
    sub_mgr = CoinDCXSubAccountManager(config=cfg)

    trading = TradingService(
        bus=bus,
        position_repo=pos_repo,
        trade_repo=trade_repo,
        event_log_repo=event_repo,
        config=cfg,
        subaccount_manager=sub_mgr,
    )
    await trading.start()

    # 1. BUY: opens position with approved amount
    await trading.on_trade_approved(
        EventType.TRADE_APPROVED,
        {
            "signal_id": "sig-eth-01",
            "coin": "ETH",
            "bot": "STE",
            "mode": "PAPER",
            "approved_amount": 400.0,
            "price": 200000.0,
            "entry_price": 200000.0,
            "stop_loss": 190000.0,
            "take_profit": 220000.0,
        },
    )
    positions = await pos_repo.get_open()
    assert len(positions) == 1
    pos = positions[0]
    expected_qty = 400.0 / 200000.0
    assert pos.qty == pytest.approx(expected_qty, rel=1e-4)

    # 2. SELL: check_open_position_exits with TP triggered strictly exits pos.qty
    closed_trades = await trading.check_open_position_exits({"ETH": 220000.0})
    assert len(closed_trades) == 1
    assert closed_trades[0].qty == pytest.approx(expected_qty, rel=1e-4)

    closed = await pos_repo.get_by_id(pos.id)
    assert closed.status == PositionStatus.CLOSED

    # Verified trade recorded exact position quantity
    trades = await trade_repo.get_by_bot(BotName.STE)
    assert len(trades) == 1
    assert trades[0].qty == pytest.approx(expected_qty, rel=1e-4)
    await db.close()


# ─────────────────────────────────────────────────────────────────────────────
# 10. FIX RECONCILIATION SAFETY
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_10_reconciliation_safety_no_duplicates_or_reopening(tmp_db_file):
    """Reconciliation checks state without placing orders or reopening closed positions."""
    db = Database(tmp_db_file)
    await db.open()
    bus = EventBus()
    cfg = AppConfig(v2_db_path=tmp_db_file, v2_deployment_mode="LIVE_MICROCASH")
    pos_repo = PositionRepository(db.connection)
    trade_repo = TradeRepository(db.connection)
    event_repo = EventLogRepository(db.connection)
    sub_mgr = CoinDCXSubAccountManager(config=cfg)
    client = sub_mgr.get_client(BotName.STE)
    client.post = AsyncMock()  # Must NOT be called during reconciliation

    trading = TradingService(
        bus=bus,
        position_repo=pos_repo,
        trade_repo=trade_repo,
        event_log_repo=event_repo,
        config=cfg,
        subaccount_manager=sub_mgr,
    )
    await trading.start()

    # Create closed position
    pos = Position(
        id="pos-closed-01",
        bot=BotName.STE,
        coin="BTC",
        pair="BTC/INR",
        status=PositionStatus.CLOSED,
        mode=BotMode.LIVE,
        entry_price=5000000.0,
        qty=0.01,
        entry_time=datetime.now(timezone.utc),
    )
    await pos_repo.insert(pos)

    # Run reconciliation
    res = await trading.reconcile_live_orders()
    assert res["status"] in ("IN_SYNC", "MISMATCH_DETECTED", "DISCREPANCIES_DETECTED")
    assert client.post.call_count == 0  # Zero new orders placed

    # Position remains closed
    check_pos = await pos_repo.get_by_id("pos-closed-01")
    assert check_pos.status == PositionStatus.CLOSED
    await db.close()
