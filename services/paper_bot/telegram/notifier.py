"""
PROJECT-ALPHA — Paper Telegram Notifier & Interactive Interface.

Dedicated Telegram interface for Paper / Simulated Trading.
Reads strictly from paper/data/alpha_paper.db with MODE="PAPER".
"""

from typing import Any
from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.config import AppConfig
from core.logging import get_logger
from telegram.formatters import (
    format_position_closed_alert,
    format_position_opened_alert,
)
from telegram.telegram import TelegramClient
from telegram.telegram_interface import TelegramInteractiveInterface
from telegram.routing import notification_mode

logger = get_logger("paper.telegram.notifier")


class PaperTelegramNotifier:
    def __init__(
        self,
        bus: EventBus,
        config: AppConfig,
        position_repo: Any | None = None,
        trade_repo: Any | None = None,
        portfolio_service: Any | None = None,
        execution_router: Any | None = None,
        signal_repo: Any | None = None,
        qpb_service: Any | None = None,
    ) -> None:
        self._bus = bus
        self._config = config
        self._position_repo = position_repo
        self._trade_repo = trade_repo
        self._portfolio_service = portfolio_service
        self._execution_router = execution_router
        self._signal_repo = signal_repo
        self._qpb_service = qpb_service

        paper_token = getattr(config, "paper_bot_token", None)
        paper_chat = getattr(config, "paper_chat_id", None)
        live_token = getattr(config, "live_bot_token", None) or getattr(config, "alert_bot_token", None)
        if paper_token and live_token and paper_token == live_token:
            logger.error("Paper Telegram disabled: PAPER and LIVE bot tokens must differ")
            paper_token = None
            paper_chat = None

        self.client = TelegramClient(
            bot_token=paper_token,
            chat_id=paper_chat,
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
            mode_override="PAPER",
            qpb_service=self._qpb_service,
        )

    async def start(self) -> None:
        self._bus.subscribe(EventType.POSITION_OPENED, self._on_position_opened)
        self._bus.subscribe(EventType.POSITION_CLOSED, self._on_position_closed)
        if self.client.is_configured:
            await self.interface.start()
            logger.info("Paper Telegram Interactive Interface started.")

    async def stop(self) -> None:
        self._bus.unsubscribe(EventType.POSITION_OPENED, self._on_position_opened)
        self._bus.unsubscribe(EventType.POSITION_CLOSED, self._on_position_closed)
        await self.interface.stop()
        logger.info("Paper Telegram Interactive Interface stopped.")

    async def _on_position_opened(self, event_type: EventType, payload: dict[str, Any]) -> None:
        try:
            if notification_mode(payload, expected_mode="PAPER") is None:
                return
            msg = format_position_opened_alert(payload)
            if self.client.is_configured:
                await self.client.send_message(msg)
            else:
                logger.info("Paper position opened alert (bot not configured): %s", msg)
        except Exception as err:
            logger.error("Failed to format or send paper position opened alert: %s", err)

    async def _on_position_closed(self, event_type: EventType, payload: dict[str, Any]) -> None:
        try:
            if notification_mode(payload, expected_mode="PAPER") is None:
                return
            msg = format_position_closed_alert(payload)
            if self.client.is_configured:
                await self.client.send_message(msg)
            else:
                logger.info("Paper position closed alert (bot not configured): %s", msg)
        except Exception as err:
            logger.error("Failed to format or send paper position closed alert: %s", err)

    async def notify_paper_trade(self, message: str, *, mode: str | None = None) -> bool:
        if notification_mode({"mode": mode}, expected_mode="PAPER") is None:
            return False
        formatted = f"📄 <b>[PAPER TRADING]</b>\n{message}"
        if self.client.is_configured:
            return bool(await self.client.send_message(formatted))
        else:
            logger.info("Paper notification (no TG service configured): %s", formatted)
        return False
