"""
tests/test_paper_exit_monitor.py — Verification of Paper Mark-to-Market Exit Monitor & Trade Persistence.

Verifies:
  1. PositionManager.update_mark_price updates current_price and unrealized PnL in DB.
  2. Take Profit trigger executes position closure, calculates net realized PnL (with statutory drag),
     persists Trade record in trades table, and broadcasts POSITION_CLOSED event.
  3. Stop Loss trigger executes position closure with loss and persists Trade record.
  4. TradeRepository safely handles external signal_id references without ForeignKey IntegrityError.
  5. PaperTelegramNotifier subscribes to POSITION_CLOSED and dispatches formatted Telegram alert.
"""

import pytest
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.config import AppConfig
from core.repository.db import Database
from core.repository.position_repo import PositionRepository
from core.repository.trade_repo import TradeRepository
from core.types import BotMode, BotName, ExitReason, Position, PositionStatus, Trade
from execution.position_manager import PositionManager
from paper.telegram.notifier import PaperTelegramNotifier


async def create_test_db(tmp_path):
    db_file = str(tmp_path / f"test_{uuid.uuid4().hex[:8]}.db")
    db = Database(db_file)
    await db.open()
    pos_repo = PositionRepository(db.connection)
    trade_repo = TradeRepository(db.connection)
    bus = EventBus()
    pos_mgr = PositionManager(
        position_repo=pos_repo,
        trade_repo=trade_repo,
        bus=bus,
    )
    return db, pos_repo, trade_repo, bus, pos_mgr


@pytest.mark.asyncio
async def test_mark_price_update_and_unrealized_pnl(tmp_path):
    db, pos_repo, trade_repo, bus, pos_mgr = await create_test_db(tmp_path)
    try:
        # Register an open position
        pos = await pos_mgr.register_position(
            bot=BotName.STE,
            coin="SOL",
            pair="SOL/USDT",
            entry_price=150.0,
            qty=2.0,
            mode=BotMode.PAPER,
            stop_loss=144.75,
            take_profit=165.0,
        )
        assert pos.id is not None

        # Update mark price
        unrealized = await pos_mgr.update_mark_price(pos, 155.0)
        assert unrealized == pytest.approx(10.0, rel=1e-4)

        # Check DB persistence
        db_pos = await pos_repo.get_by_id(pos.id)
        assert db_pos is not None
        assert db_pos.current_price == 155.0
        assert db_pos.unrealised_pnl == pytest.approx(10.0, rel=1e-4)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_take_profit_exit_execution_and_trade_record(tmp_path):
    db, pos_repo, trade_repo, bus, pos_mgr = await create_test_db(tmp_path)
    try:
        closed_events = []

        async def _on_closed(event_type, payload):
            closed_events.append(payload)

        bus.subscribe(EventType.POSITION_CLOSED, _on_closed)

        pos = await pos_mgr.register_position(
            bot=BotName.STE,
            coin="JUP",
            pair="JUP/USDT",
            entry_price=0.30,
            qty=100.0,
            mode=BotMode.PAPER,
            stop_loss=0.2895,  # -3.5%
            take_profit=0.33,   # +10.0%
            signal_id="sig_external_12345",
        )

        # Evaluate brackets with price breaching Take Profit
        triggers = await pos_mgr.evaluate_brackets("JUP/USDT", 0.335)
        assert len(triggers) == 1
        trig_pos, reason, exit_px = triggers[0]
        assert trig_pos.id == pos.id
        assert reason == ExitReason.TAKE_PROFIT
        assert exit_px == 0.335

        # Close position
        closed_pos, trade = await pos_mgr.close_position(
            position_id=pos.id,
            exit_price=exit_px,
            exit_reason=reason,
        )

        assert closed_pos is not None
        assert trade is not None

        # Verify DB position state
        db_pos = await pos_repo.get_by_id(pos.id)
        assert db_pos.status == PositionStatus.CLOSED
        assert db_pos.exit_price == 0.335
        assert db_pos.exit_reason == ExitReason.TAKE_PROFIT
        assert db_pos.realized_pnl > 0.0

        # Verify Trade record in trades table
        db_trade = await trade_repo.get_by_id(trade.id)
        assert db_trade is not None
        assert db_trade.coin == "JUP"
        assert db_trade.exit_price == 0.335
        assert db_trade.exit_reason == ExitReason.TAKE_PROFIT
        assert db_trade.pnl > 0.0
        assert db_trade.mode == BotMode.PAPER

        # Verify EventBus event
        assert len(closed_events) == 1
        ev = closed_events[0]
        assert ev["coin"] == "JUP"
        assert ev["mode"] == "PAPER"
        assert ev["exit_reason"] == "TAKE_PROFIT"
        assert "net_pnl" in ev
        assert "pnl" in ev
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_stop_loss_exit_execution(tmp_path):
    db, pos_repo, trade_repo, bus, pos_mgr = await create_test_db(tmp_path)
    try:
        pos = await pos_mgr.register_position(
            bot=BotName.VCP,
            coin="PARTI",
            pair="PARTI/USDT",
            entry_price=0.030,
            qty=1000.0,
            mode=BotMode.PAPER,
            stop_loss=0.02895,
            take_profit=0.036,
        )

        # Price drops below SL
        triggers = await pos_mgr.evaluate_brackets("PARTI/USDT", 0.028)
        assert len(triggers) == 1
        trig_pos, reason, exit_px = triggers[0]
        assert reason == ExitReason.STOP_LOSS
        assert exit_px == 0.028

        closed_pos, trade = await pos_mgr.close_position(
            position_id=pos.id,
            exit_price=exit_px,
            exit_reason=reason,
        )

        assert closed_pos is not None
        assert trade is not None
        assert trade.pnl < 0.0

        db_trade = await trade_repo.get_by_id(trade.id)
        assert db_trade is not None
        assert db_trade.exit_reason == ExitReason.STOP_LOSS
        assert db_trade.pnl < 0.0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_trade_repo_foreign_key_safety_external_signal(tmp_path):
    db, pos_repo, trade_repo, bus, pos_mgr = await create_test_db(tmp_path)
    try:
        pos = await pos_mgr.register_position(
            bot=BotName.STE,
            coin="BTC",
            pair="BTC/INR",
            entry_price=8000000.0,
            qty=0.01,
            mode=BotMode.PAPER,
            stop_loss=7720000.0,
            take_profit=8400000.0,
        )

        # Create a trade with an external signal_id that does not exist in local signals table
        trade = Trade(
            id=str(uuid.uuid4()),
            position_id=pos.id,
            bot=BotName.STE,
            coin="BTC",
            pair="BTC/INR",
            entry_price=8000000.0,
            exit_price=8400000.0,
            qty=0.01,
            pnl=3500.0,
            pnl_pct=4.37,
            entry_time=datetime.now(timezone.utc),
            exit_time=datetime.now(timezone.utc),
            exit_reason=ExitReason.TAKE_PROFIT,
            mode=BotMode.PAPER,
            signal_id="non_existent_scanner_signal_uuid",
        )

        trade_id = await trade_repo.insert(trade)
        assert trade_id == trade.id

        inserted = await trade_repo.get_by_id(trade.id)
        assert inserted is not None
        assert inserted.coin == "BTC"
        assert inserted.pnl == 3500.0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_paper_telegram_notifier_position_closed_alert():
    bus = EventBus()
    config = AppConfig()
    config.paper_bot_token = "123456:FAKE_TOKEN"
    config.paper_chat_id = "987654321"

    notifier = PaperTelegramNotifier(bus=bus, config=config)
    notifier.client.send_message = AsyncMock(return_value=True)

    await notifier.start()

    payload = {
        "trade_id": "trd_001",
        "position_id": "pos_001",
        "bot": "STE",
        "coin": "SOL",
        "pair": "SOL/USDT",
        "entry_price": 150.0,
        "exit_price": 165.0,
        "qty": 2.0,
        "pnl": 28.50,
        "pnl_pct": 9.50,
        "exit_reason": "TAKE_PROFIT",
        "mode": "PAPER",
    }

    await bus.publish(EventType.POSITION_CLOSED, payload)

    assert notifier.client.send_message.called
    sent_msg = notifier.client.send_message.call_args[0][0]
    assert "Position Closed — SOL" in sent_msg
    assert "TAKE_PROFIT" in sent_msg
    assert "$28.50" in sent_msg or "28.50" in sent_msg

    await notifier.stop()
