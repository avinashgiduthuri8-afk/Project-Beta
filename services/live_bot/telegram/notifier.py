"""
PROJECT-ALPHA — Live Telegram Notifier & Interactive Interface.

Dedicated Telegram interface for Real Money Live Trading.
Reads strictly from live/data/alpha_live.db with MODE="LIVE".
"""

from typing import Any
from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.config import AppConfig
from core.logging import get_logger
from telegram.formatters import format_position_closed_alert, format_position_opened_alert
from telegram.routing import notification_mode
from telegram.telegram import TelegramClient
from telegram.telegram_interface import TelegramInteractiveInterface

logger = get_logger("live.telegram.notifier")


class LiveTelegramNotifier:
    def __init__(
        self,
        bus: EventBus,
        config: AppConfig,
        position_repo: Any | None = None,
        trade_repo: Any | None = None,
        portfolio_service: Any | None = None,
        execution_router: Any | None = None,
        signal_repo: Any | None = None,
    ) -> None:
        self._bus = bus
        self._config = config
        self._position_repo = position_repo
        self._trade_repo = trade_repo
        self._portfolio_service = portfolio_service
        self._execution_router = execution_router
        self._signal_repo = signal_repo

        live_token = getattr(config, "live_bot_token", None) or getattr(config, "alert_bot_token", None)
        live_chat = getattr(config, "live_chat_id", None) or getattr(config, "alert_chat_id", None)
        paper_token = getattr(config, "paper_bot_token", None)
        if live_token and paper_token and live_token == paper_token:
            logger.error("Live Telegram disabled: LIVE and PAPER bot tokens must differ")
            live_token = None
            live_chat = None

        self.client = TelegramClient(
            bot_token=live_token,
            chat_id=live_chat,
            resolve_default_credentials=False,
        )
        self.interface = TelegramInteractiveInterface(
            telegram_client=self.client,
            bus=self._bus,
            config=self._config,
            signal_repo=self._signal_repo,
            position_repo=self._position_repo,
            trade_repo=self._trade_repo,
            portfolio_service=self._portfolio_service,
            execution_router=self._execution_router,
            mode_override="LIVE",
        )

    async def start(self) -> None:
        self._bus.subscribe(EventType.POSITION_OPENED, self._on_position_opened)
        self._bus.subscribe(EventType.POSITION_CLOSED, self._on_position_closed)
        if self.client.is_configured:
            await self.interface.start()
            logger.info("Live Telegram Interactive Interface started.")

    async def stop(self) -> None:
        self._bus.unsubscribe(EventType.POSITION_OPENED, self._on_position_opened)
        self._bus.unsubscribe(EventType.POSITION_CLOSED, self._on_position_closed)
        await self.interface.stop()
        logger.info("Live Telegram Interactive Interface stopped.")

    async def _on_position_opened(self, event_type: EventType, payload: dict[str, Any]) -> None:
        if notification_mode(payload, expected_mode="LIVE") is None:
            return
        if self.client.is_configured:
            await self.client.send_message(format_position_opened_alert(payload))

    async def _on_position_closed(self, event_type: EventType, payload: dict[str, Any]) -> None:
        if notification_mode(payload, expected_mode="LIVE") is None:
            return
        if self.client.is_configured:
            await self.client.send_message(format_position_closed_alert(payload))

    async def notify_live_trade(self, message: str, *, mode: str | None = None) -> bool:
        if notification_mode({"mode": mode}, expected_mode="LIVE") is None:
            return False
        formatted = f"🚨 <b>[LIVE TRADING]</b>\n{message}"
        if self.client.is_configured:
            return bool(await self.client.send_message(formatted))
        else:
            logger.info("Live notification (no TG service configured): %s", formatted)
        return False
