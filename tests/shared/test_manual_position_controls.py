"""
Unit and Integration Tests for Manual Position Controls, Profit Trailing, and Default Trade Amount (₹200).
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

# ── 1. Default Trade Amount Verification ────────────────────────────────────


def test_default_trade_amount_is_200():
    cfg = AppConfig()
    assert cfg.order_size_inr == 200.0
    assert cfg.v2_default_trade_amount_ste == 200.0
    assert cfg.v2_default_trade_amount_hda == 200.0
    assert cfg.v2_default_trade_amount_vcp == 200.0
    assert cfg.v2_default_trade_amount_bbs == 200.0


# ── 2. Manual Close Position & Friction Verification ────────────────────────


@pytest.mark.anyio
async def test_manual_close_position_lifecycle(tmp_path):
    db_path = str(tmp_path / f"test_man_close_{uuid.uuid4().hex[:6]}.db")
    db = Database(db_path)
    await db.open()
    try:
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

        # 1. Insert an active open position (ETH @ ₹2,50,000, Qty: 0.0008, ₹200 notional)
        now = datetime.now(timezone.utc)
        pos = Position(
            id="pos-eth-test-01",
            bot=BotName.STE,
            coin="ETH",
            pair="ETH/INR",
            qty=0.0008,
            entry_price=250000.0,
            entry_time=now,
            mode=BotMode.PAPER,
            status=PositionStatus.OPEN,
            current_price=250000.0,
            stop_loss=245000.0,
            take_profit=260000.0,
        )
        await pos_repo.insert(pos)

        # Track bus event
        closed_events = []

        async def on_pos_closed(ev, data):
            closed_events.append(data)

        bus.subscribe(EventType.POSITION_CLOSED, on_pos_closed)

        # 2. Execute manual close at LTP ₹2,55,000 (+2.0% gross)
        res = await trading_svc.manual_close_position(
            position_id="pos-eth-test-01",
            exit_price=255000.0,
            reason="MANUAL",
        )

        assert res["success"] is True
        assert res["position_id"] == "pos-eth-test-01"
        assert res["exit_price"] == 255000.0
        assert res["pnl"] > 0.0

        # Position status in DB must now be CLOSED
        db_pos = await pos_repo.get_by_id("pos-eth-test-01")
        assert db_pos.status == PositionStatus.CLOSED
        assert db_pos.exit_reason == ExitReason.MANUAL
        assert db_pos.exit_price == 255000.0

        # Trade must be persisted in TradeRepository
        trades = await trade_repo.get_by_position("pos-eth-test-01")
        assert len(trades) == 1
        assert trades[0].exit_reason == ExitReason.MANUAL
        assert trades[0].exit_price == 255000.0

        await asyncio.sleep(0.02)
        assert len(closed_events) == 1
        assert closed_events[0]["exit_reason"] == "MANUAL"

        await trading_svc.stop()
    finally:
        await db.close()


# ── 3. Modify Position Targets & Trailing Stop ───────────────────────────────


@pytest.mark.anyio
async def test_modify_position_targets_and_trailing(tmp_path):
    db_path = str(tmp_path / f"test_mod_pos_{uuid.uuid4().hex[:6]}.db")
    db = Database(db_path)
    await db.open()
    try:
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

        # 1. Insert open position
        now = datetime.now(timezone.utc)
        pos = Position(
            id="pos-sol-test-02",
            bot=BotName.HDA,
            coin="SOL",
            pair="SOL/INR",
            qty=0.016,
            entry_price=12500.0,
            entry_time=now,
            mode=BotMode.PAPER,
            status=PositionStatus.OPEN,
            current_price=12500.0,
            stop_loss=12200.0,
            take_profit=13200.0,
        )
        await pos_repo.insert(pos)

        # 2. Modify targets: raise SL to ₹12,400, TP to ₹13,500, set Trailing Stop to 2.0%
        res = await trading_svc.modify_position_targets(
            position_id="pos-sol-test-02",
            stop_loss=12400.0,
            take_profit=13500.0,
            trailing_stop_pct=2.0,
        )

        assert res["success"] is True
        assert res["stop_loss"] == 12400.0
        assert res["take_profit"] == 13500.0

        # Verify DB updated
        db_pos = await pos_repo.get_by_id("pos-sol-test-02")
        assert db_pos.stop_loss == 12400.0
        assert db_pos.take_profit == 13500.0

        # 3. Simulate price rising to ₹13,000 -> Trailing stop rises to 13000 * 0.98 = 12740
        new_trailing = await trading_svc.position_manager.update_trailing_stop(
            position_id="pos-sol-test-02",
            current_price=13000.0,
            trailing_pct=0.02,
        )
        assert new_trailing == 12740.0
        assert (
            trading_svc.position_manager._trailing_stops["pos-sol-test-02"] == 12740.0
        )

        await trading_svc.stop()
    finally:
        await db.close()
