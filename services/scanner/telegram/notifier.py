"""
PROJECT-ALPHA — Scanner Telegram Notifier & Interactive Interface.

Dedicated Telegram interface for Alpha-Scanner.
Uses tokens previously assigned to Live bot to dispatch scanner signal alerts
and run interactive C2 commands (/scan, /signals, /funnel, /status, /help).
"""

from typing import Any
from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.config import AppConfig
from core.logging import get_logger
from telegram.telegram import TelegramClient
from telegram.telegram_interface import TelegramInteractiveInterface

logger = get_logger("scanner.telegram.notifier")


def format_scanner_signal_alert(payload: dict[str, Any]) -> str:
    coin = payload.get("coin", "UNKNOWN")
    pair = payload.get("pair", f"{coin}/INR")
    strategy = payload.get("strategy") or payload.get("bot", "STE")
    score = payload.get("confluence_score") or payload.get("score", 0)
    price = float(payload.get("price") or payload.get("entry_price") or payload.get("entry") or 0.0)
    stop_loss = float(payload.get("stop_loss") or 0.0)
    take_profit = float(payload.get("take_profit") or 0.0)
    sl_pct = ((price - stop_loss) / price * 100) if price > 0 and stop_loss > 0 else 0.0
    tp_pct = ((take_profit - price) / price * 100) if price > 0 and take_profit > 0 else 0.0
    reason = payload.get("signal_reason", "")
    timeframe = payload.get("timeframe", "1h")

    lines = [
        f"📡 <b>NEW SCANNER SIGNAL — {coin}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"<b>Pair:</b> <code>{pair}</code>",
        f"<b>Strategy:</b> <code>{strategy}</code> ({timeframe})",
        f"<b>Confluence Score:</b> <code>{score}/100</code>",
        f"<b>Price:</b> <code>{price:.4f}</code>",
    ]
    if take_profit > 0:
        lines.append(f"<b>Take Profit (TP):</b> <code>{take_profit:.4f}</code> (+{tp_pct:.1f}%)")
    if stop_loss > 0:
        lines.append(f"<b>Stop Loss (SL):</b> <code>{stop_loss:.4f}</code> (-{sl_pct:.1f}%)")
    if reason:
        lines.append(f"<b>Catalyst:</b> <i>{reason}</i>")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("<i>Mode: Forward Tracking (Live Execution Disabled)</i>")
    return "\n".join(lines)


class ScannerTelegramNotifier:
    def __init__(
        self,
        bus: EventBus,
        config: AppConfig,
        signal_repo: Any | None = None,
        scanner_service: Any | None = None,
        event_log_repo: Any | None = None,
    ) -> None:
        self._bus = bus
        self._config = config
        self._signal_repo = signal_repo
        self._scanner_service = scanner_service
        self._event_log_repo = event_log_repo

        # Reassign Live Trade bot token/chat to Scanner Bot
        scanner_token = (
            getattr(config, "scanner_bot_token", None)
            or getattr(config, "live_bot_token", None)
            or getattr(config, "alert_bot_token", None)
        )
        scanner_chat = (
            getattr(config, "scanner_chat_id", None)
            or getattr(config, "live_chat_id", None)
            or getattr(config, "alert_chat_id", None)
        )

        self.client = TelegramClient(
            bot_token=scanner_token,
            chat_id=scanner_chat,
            resolve_default_credentials=False,
        )
        self.interface = TelegramInteractiveInterface(
            telegram_client=self.client,
            bus=self._bus,
            config=self._config,
            signal_repo=self._signal_repo,
            scanner_service=self._scanner_service,
            event_log_repo=self._event_log_repo,
            mode_override="SCANNER",
        )

    async def start(self) -> None:
        self._bus.subscribe(EventType.SIGNAL_GENERATED, self._on_signal_generated)
        if self.client.is_configured:
            await self.interface.start()
            logger.info("Scanner Telegram Interactive Interface started.")
        else:
            logger.info("Scanner Telegram client not configured; notifications disabled.")

    async def stop(self) -> None:
        self._bus.unsubscribe(EventType.SIGNAL_GENERATED, self._on_signal_generated)
        await self.interface.stop()
        logger.info("Scanner Telegram Interactive Interface stopped.")

    async def _on_signal_generated(self, event_type: EventType, payload: dict[str, Any]) -> None:
        try:
            msg = format_scanner_signal_alert(payload)
            if self.client.is_configured:
                await self.client.send_message(msg)
            else:
                logger.info("Scanner signal generated (bot not configured): %s", msg)
        except Exception as err:
            logger.error("Failed to send scanner signal alert to Telegram: %s", err)
