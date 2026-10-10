"""
Test Suite for PROJECT-ALPHA Telegram Interactive C2 Interface.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest

from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.config import AppConfig
from core.repository.db import Database
from core.repository.position_repo import PositionRepository
from core.repository.signal_repo import SignalRepository
from core.repository.trade_repo import TradeRepository
from core.types import BotMode, BotName, Position, PositionStatus
from execution.risk import RiskService
from telegram import (
    NotificationService,
    TelegramClient,
    TelegramInteractiveInterface,
    format_telegram_bot_fleet,
    format_telegram_help,
    format_telegram_menu,
    format_telegram_pipeline_stages,
    format_telegram_portfolio,
    format_telegram_positions,
)


class MockTelegramClient(TelegramClient):
    """Mock Telegram client capturing sent and edited messages."""

    def __init__(
        self, bot_token: str = "mock-bot-token", chat_id: str = "123456789"
    ) -> None:
        super().__init__(bot_token=bot_token, chat_id=chat_id)
        self.sent_messages: list[dict[str, Any]] = []
        self.edited_messages: list[dict[str, Any]] = []
        self.answered_callbacks: list[dict[str, Any]] = []
        self.mock_updates: list[dict[str, Any]] = []

    async def send_message(
        self,
        text: str,
        target_chat_id: str | None = None,
        parse_mode: str = "HTML",
        reply_markup: dict | None = None,
        max_retries: int = 2,
    ) -> bool:
        self.sent_messages.append(
            {
                "text": text,
                "chat_id": target_chat_id or self._chat_id,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup,
            }
        )
        return True

    async def edit_message_text(
        self,
        text: str,
        chat_id: str | int,
        message_id: int,
        parse_mode: str = "HTML",
        reply_markup: dict | None = None,
        max_retries: int = 2,
    ) -> bool:
        self.edited_messages.append(
            {
                "text": text,
                "chat_id": chat_id,
                "message_id": message_id,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup,
            }
        )
        return True

    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> bool:
        self.answered_callbacks.append(
            {
                "id": callback_query_id,
                "text": text,
                "show_alert": show_alert,
            }
        )
        return True

    async def get_updates(
        self,
        offset: int | None = None,
        timeout: int = 20,
        limit: int = 100,
    ) -> list[dict]:
        updates = list(self.mock_updates)
        self.mock_updates.clear()
        return updates


def test_telegram_formatters():
    """Verify all Telegram HTML/emoji formatters render expected content."""
    overview = {
        "status": "HEALTHY",
        "total_aum": 100000.0,
        "total_deployed": 25000.0,
        "daily_pnl": 1250.5,
        "active_positions": 2,
        "bot_count": 4,
        "trading_mode": "PAPER ACTIVE",
    }
    menu_txt = format_telegram_menu(overview)
    assert "PROJECT-ALPHA" in menu_txt
    assert "₹100,000.00" in menu_txt
    assert "+₹1,250.50" in menu_txt

    bots_data = [
        {
            "name": "STE",
            "subaccount_id": "ALPHA_STE_01",
            "current_stage": "SCANNER",
            "wallet_balance": 35000.0,
            "available_balance": 30000.0,
            "open_positions": 1,
            "daily_pnl": 500.0,
            "win_rate_pct": 75.0,
        },
        {
            "name": "HDA",
            "subaccount_id": "ALPHA_HDA_01",
            "current_stage": "IDLE",
            "wallet_balance": 30000.0,
            "available_balance": 30000.0,
            "open_positions": 0,
            "daily_pnl": 0.0,
            "win_rate_pct": 80.0,
        },
    ]
    bots_txt = format_telegram_bot_fleet(bots_data)
    assert "BOT FLEET" in bots_txt
    assert "STE" in bots_txt
    assert "HDA" in bots_txt

    stages_data = [
        {
            "number": 1,
            "name": "Market Data",
            "status": "ACTIVE",
            "processed_count": 100,
            "rejected_count": 0,
        },
        {
            "number": 2,
            "name": "5-Layer Confluence Scanner",
            "status": "ACTIVE",
            "processed_count": 100,
            "rejected_count": 98,
        },
    ]
    stages_txt = format_telegram_pipeline_stages(stages_data)
    assert "11-STAGE AUTONOMOUS PIPELINE" in stages_txt
    assert "Filtered: <code>98</code>" in stages_txt

    portfolio_data = {
        "total_aum": 100000.0,
        "total_deployed": 20000.0,
        "total_cash": 80000.0,
        "total_unrealised_pnl": 450.0,
        "total_realised_pnl": 1200.0,
        "daily_pnl": 1200.0,
        "capital_utilisation": 20.0,
    }
    port_txt = format_telegram_portfolio(portfolio_data)
    assert "PORTFOLIO & CAPITAL ALLOCATION" in port_txt
    assert "₹80,000.00" in port_txt

    positions_data = [
        {
            "coin": "BTC",
            "bot": "STE",
            "qty": 0.05,
            "entry_price": 5000000.0,
            "current_price": 5100000.0,
            "unrealised_pnl": 5000.0,
            "stop_loss": 4900000.0,
            "take_profit": 5230000.0,
        },
    ]
    pos_txt = format_telegram_positions(positions_data)
    assert "BTC/INR" in pos_txt
    assert "+₹5000.00" in pos_txt

    help_txt = format_telegram_help()
    assert "/start" in help_txt
    assert "/bots" in help_txt
    assert "/emergency_stop" in help_txt


@pytest.mark.anyio
async def test_telegram_authorization(tmp_path):
    """Verify whitelist authorization rules for Telegram chat IDs."""
    cfg = AppConfig(
        v2_db_path=str(tmp_path / "test_auth.db"),
        alert_bot_token="test-token",
        alert_chat_id="1001",
        telegram_allowed_chat_ids="1002, 1003",
    )
    bus = EventBus()
    mock_client = MockTelegramClient()
    tg_iface = TelegramInteractiveInterface(
        telegram_client=mock_client,
        bus=bus,
        config=cfg,
    )

    assert tg_iface.is_authorized("1001") is True
    assert tg_iface.is_authorized(1002) is True
    assert tg_iface.is_authorized("1003") is True
    assert tg_iface.is_authorized("9999") is False


@pytest.mark.anyio
async def test_telegram_command_routing(tmp_path):
    """Verify Telegram commands render and dispatch proper replies."""
    db_path = str(tmp_path / "test_tg.db")
    db = Database(db_path)
    await db.open()
    try:
        conn = db.connection
        bus = EventBus()
        sig_repo = SignalRepository(conn)
        pos_repo = PositionRepository(conn)
        trade_repo = TradeRepository(conn)
        cfg = AppConfig(
            v2_db_path=db_path,
            alert_bot_token="test-token",
            alert_chat_id="12345",
        )

        mock_client = MockTelegramClient(bot_token="test-token", chat_id="12345")
        tg_iface = TelegramInteractiveInterface(
            telegram_client=mock_client,
            bus=bus,
            config=cfg,
            signal_repo=sig_repo,
            position_repo=pos_repo,
            trade_repo=trade_repo,
        )

        # 1. Test /start
        await tg_iface._handle_incoming_message(
            {"chat": {"id": 12345}, "text": "/start"}
        )
        assert len(mock_client.sent_messages) == 1
        assert "PROJECT-ALPHA" in mock_client.sent_messages[-1]["text"]
        assert mock_client.sent_messages[-1]["reply_markup"] is not None

        # 2. Test /bots
        await tg_iface._handle_incoming_message(
            {"chat": {"id": 12345}, "text": "/bots"}
        )
        assert "BOT FLEET" in mock_client.sent_messages[-1]["text"]
        assert "STE" in mock_client.sent_messages[-1]["text"]

        # 3. Test /stages
        await tg_iface._handle_incoming_message(
            {"chat": {"id": 12345}, "text": "/stages"}
        )
        assert "11-STAGE AUTONOMOUS PIPELINE" in mock_client.sent_messages[-1]["text"]

        # 4. Test /positions (empty)
        await tg_iface._handle_incoming_message(
            {"chat": {"id": 12345}, "text": "/positions"}
        )
        assert "No active open positions" in mock_client.sent_messages[-1]["text"]

        # 5. Insert position and test /positions again
        pos = Position(
            id="pos-tg-01",
            bot=BotName.STE,
            coin="SOL",
            pair="SOL/INR",
            qty=2.5,
            entry_price=15000.0,
            entry_time=datetime.now(timezone.utc),
            mode=BotMode.PAPER,
            status=PositionStatus.OPEN,
            current_price=15500.0,
            unrealised_pnl=1250.0,
            stop_loss=14500.0,
            take_profit=16000.0,
        )
        await pos_repo.insert(pos)
        await tg_iface._handle_incoming_message(
            {"chat": {"id": 12345}, "text": "/positions"}
        )
        assert "SOL/INR" in mock_client.sent_messages[-1]["text"]
        assert "1250.00" in mock_client.sent_messages[-1]["text"]

        # 6. Test /help
        await tg_iface._handle_incoming_message(
            {"chat": {"id": 12345}, "text": "/help"}
        )
        assert "Commands" in mock_client.sent_messages[-1]["text"]

        # 7. Test unauthorized sender gets rejected
        await tg_iface._handle_incoming_message(
            {"chat": {"id": 99999}, "text": "/start"}
        )
        assert (
            "Unauthorized." in mock_client.sent_messages[-1]["text"]
            or "Access Denied" in mock_client.sent_messages[-1]["text"]
        )
    finally:
        await db.close()


@pytest.mark.anyio
async def test_telegram_callback_queries(tmp_path):
    """Verify inline button callback queries perform in-place edits and actions."""
    db_path = str(tmp_path / "test_cb.db")
    db = Database(db_path)
    await db.open()
    try:
        conn = db.connection
        bus = EventBus()
        pos_repo = PositionRepository(conn)
        trade_repo = TradeRepository(conn)
        cfg = AppConfig(
            v2_db_path=db_path,
            alert_bot_token="test-token",
            alert_chat_id="12345",
        )

        mock_client = MockTelegramClient(bot_token="test-token", chat_id="12345")
        risk_svc = RiskService(bus, pos_repo, trade_repo, None, cfg)
        tg_iface = TelegramInteractiveInterface(
            telegram_client=mock_client,
            bus=bus,
            config=cfg,
            position_repo=pos_repo,
            trade_repo=trade_repo,
            risk_service=risk_svc,
        )

        # 1. Tap Bot Fleet button (cb:bots)
        cb_bots = {
            "id": "cb_01",
            "data": "cb:bots",
            "message": {"chat": {"id": 12345}, "message_id": 101},
        }
        await tg_iface._handle_callback_query(cb_bots)
        assert len(mock_client.edited_messages) == 1
        assert "BOT FLEET" in mock_client.edited_messages[-1]["text"]
        assert len(mock_client.answered_callbacks) == 1

        # 2. Tap 11 Stages button (cb:stages)
        cb_stages = {
            "id": "cb_02",
            "data": "cb:stages",
            "message": {"chat": {"id": 12345}, "message_id": 101},
        }
        await tg_iface._handle_callback_query(cb_stages)
        assert "11-STAGE AUTONOMOUS PIPELINE" in mock_client.edited_messages[-1]["text"]

        # 3. Tap Emergency Stop (cb:stop)
        cb_stop = {
            "id": "cb_03",
            "data": "cb:stop",
            "message": {"chat": {"id": 12345}, "message_id": 101},
        }
        await tg_iface._handle_callback_query(cb_stop)
        assert "EMERGENCY STOP CONFIRMATION" in mock_client.edited_messages[-1]["text"]

        # 4. Confirm Emergency Stop (cb:confirm_stop)
        cb_confirm = {
            "id": "cb_04",
            "data": "cb:confirm_stop",
            "message": {"chat": {"id": 12345}, "message_id": 101},
        }
        await tg_iface._handle_callback_query(cb_confirm)
        assert "EMERGENCY STOP" in mock_client.edited_messages[-1]["text"]
        risk_state = await risk_svc.get_state()
        assert risk_state.circuit_breaker_open is True

        # 5. Resume Trading (cb:resume)
        cb_resume = {
            "id": "cb_05",
            "data": "cb:resume",
            "message": {"chat": {"id": 12345}, "message_id": 101},
        }
        await tg_iface._handle_callback_query(cb_resume)
        assert "TRADING EXECUTION RESUMED" in mock_client.edited_messages[-1]["text"]
        risk_state_resumed = await risk_svc.get_state()
        assert risk_state_resumed.circuit_breaker_open is False
    finally:
        await db.close()


@pytest.mark.anyio
async def test_notification_service_with_interactive_telegram(tmp_path):
    """Verify NotificationService initializes, wires dependencies, and triggers lifecycle."""
    db_path = str(tmp_path / "test_notif.db")
    db = Database(db_path)
    await db.open()
    try:
        conn = db.connection
        bus = EventBus()
        sig_repo = SignalRepository(conn)
        pos_repo = PositionRepository(conn)
        trade_repo = TradeRepository(conn)
        cfg = AppConfig(
            v2_db_path=db_path,
            alert_bot_token="test-token",
            alert_chat_id="12345",
            telegram_interactive_enabled=True,
        )

        mock_client = MockTelegramClient(bot_token="test-token", chat_id="12345")
        notif_svc = NotificationService(
            bus=bus,
            config=cfg,
            telegram_client=mock_client,
            signal_repo=sig_repo,
            position_repo=pos_repo,
            trade_repo=trade_repo,
        )

        await notif_svc.start()
        assert notif_svc.interactive_interface._running is True

        # Dispatch a trade event to test outbound alerts push alongside interactive interface
        await bus.publish(
            EventType.POSITION_OPENED,
            {
                "mode": "LIVE",
                "service": "ALPHA_LIVE",
                "coin": "ETH",
                "bot": "HDA",
                "entry_price": 300000.0,
                "qty": 0.1,
                "stop_loss": 290000.0,
                "take_profit": 320000.0,
            },
        )
        await asyncio.sleep(0.1)
        assert len(mock_client.sent_messages) >= 1
        assert "BUY EXECUTED" in mock_client.sent_messages[-1]["text"]
        assert "ETH" in mock_client.sent_messages[-1]["text"]

        await notif_svc.stop()
        assert notif_svc.interactive_interface._running is False
    finally:
        await db.close()
