"""Regression coverage for strict PAPER/LIVE Telegram notification isolation."""

from unittest.mock import AsyncMock

import pytest

from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.config import AppConfig
from live.telegram.notifier import LiveTelegramNotifier
from paper.telegram.notifier import PaperTelegramNotifier
from telegram.service import NotificationService


def _config() -> AppConfig:
    config = AppConfig(_env_file=None)
    config.paper_bot_token = "paper-token"
    config.paper_chat_id = "paper-chat"
    config.live_bot_token = "live-token"
    config.live_chat_id = "live-chat"
    config.alert_bot_token = "live-token"
    config.alert_chat_id = "live-chat"
    return config


def _service() -> NotificationService:
    service = NotificationService(bus=EventBus(), config=_config())
    service.paper_telegram_client.send_message = AsyncMock(return_value=True)
    service.live_telegram_client.send_message = AsyncMock(return_value=True)
    return service


@pytest.mark.asyncio
async def test_paper_notification_cannot_reach_live_bot():
    service = _service()
    live_notifier = LiveTelegramNotifier(bus=EventBus(), config=_config())
    live_notifier.client.send_message = AsyncMock(return_value=True)

    assert await service.send_custom_alert("PAPER_TEST", mode="PAPER") is True
    await live_notifier._on_position_opened(
        EventType.POSITION_OPENED,
        {"mode": "PAPER", "service": "ALPHA_PAPER", "coin": "SOL"},
    )

    service.paper_telegram_client.send_message.assert_awaited_once_with("PAPER_TEST")
    service.live_telegram_client.send_message.assert_not_awaited()
    live_notifier.client.send_message.assert_not_awaited()
    assert service.paper_telegram_client.bot_token == "paper-token"
    assert service.paper_telegram_client.default_chat_id == "paper-chat"


@pytest.mark.asyncio
async def test_live_notification_cannot_reach_paper_bot():
    service = _service()
    paper_notifier = PaperTelegramNotifier(bus=EventBus(), config=_config())
    paper_notifier.client.send_message = AsyncMock(return_value=True)

    assert await service.send_custom_alert("LIVE_TEST", mode="LIVE") is True
    await paper_notifier._on_position_opened(
        EventType.POSITION_OPENED,
        {"mode": "LIVE", "service": "ALPHA_LIVE", "coin": "BTC"},
    )

    service.live_telegram_client.send_message.assert_awaited_once_with("LIVE_TEST")
    service.paper_telegram_client.send_message.assert_not_awaited()
    paper_notifier.client.send_message.assert_not_awaited()
    assert service.live_telegram_client.bot_token == "live-token"
    assert service.live_telegram_client.default_chat_id == "live-chat"


@pytest.mark.asyncio
async def test_missing_mode_notification_rejected():
    service = _service()
    paper_notifier = PaperTelegramNotifier(bus=EventBus(), config=_config())
    live_notifier = LiveTelegramNotifier(bus=EventBus(), config=_config())
    paper_notifier.client.send_message = AsyncMock(return_value=True)
    live_notifier.client.send_message = AsyncMock(return_value=True)

    assert await service.send_custom_alert("UNKNOWN_TEST", mode=None) is False
    await service._on_position_opened(EventType.POSITION_OPENED, {"coin": "BTC"})
    await service._on_position_opened(
        EventType.POSITION_OPENED, {"coin": "BTC", "mode": "UNKNOWN"}
    )
    await paper_notifier._on_position_opened(EventType.POSITION_OPENED, {"coin": "SOL"})
    await live_notifier._on_position_opened(EventType.POSITION_OPENED, {"coin": "BTC"})

    service.paper_telegram_client.send_message.assert_not_awaited()
    service.live_telegram_client.send_message.assert_not_awaited()
    paper_notifier.client.send_message.assert_not_awaited()
    live_notifier.client.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_paper_execution_alert_routes_to_paper():
    notifier = PaperTelegramNotifier(bus=EventBus(), config=_config())
    notifier.client.send_message = AsyncMock(return_value=True)

    payload = {
        "mode": "PAPER",
        "service": "ALPHA_PAPER",
        "coin": "SOL",
        "pair": "SOL/INR",
        "entry_price": 100.0,
        "qty": 2.0,
    }
    await notifier._on_position_opened(EventType.POSITION_OPENED, payload)

    notifier.client.send_message.assert_awaited_once()
    assert notifier.client.bot_token == "paper-token"
    assert notifier.client.default_chat_id == "paper-chat"


@pytest.mark.asyncio
async def test_live_execution_alert_routes_to_live():
    notifier = LiveTelegramNotifier(bus=EventBus(), config=_config())
    notifier.client.send_message = AsyncMock(return_value=True)

    payload = {
        "mode": "LIVE",
        "service": "ALPHA_LIVE",
        "coin": "BTC",
        "pair": "BTC/INR",
        "entry_price": 100.0,
        "qty": 2.0,
    }
    await notifier._on_position_opened(EventType.POSITION_OPENED, payload)

    notifier.client.send_message.assert_awaited_once()
    assert notifier.client.bot_token == "live-token"
    assert notifier.client.default_chat_id == "live-chat"
