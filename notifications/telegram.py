"""Telegram Bot Notifier for Execution Alerts."""

from __future__ import annotations

import logging
import requests
from typing import Optional
from core.interfaces import BaseNotifier

logger = logging.getLogger(__name__)


class TelegramNotifier(BaseNotifier):
    """Dispatches trade fills, SL executions, and RMS alerts to Telegram channel/group."""

    def __init__(self, bot_token: Optional[str] = None, chat_id: Optional[str] = None, enabled: bool = False):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = enabled and bool(bot_token and chat_id)

    def send_alert(self, title: str, message: str, level: str = "INFO") -> bool:
        if not self.enabled:
            logger.debug(f"[Telegram Mock] {level} | {title}: {message}")
            return True

        text = f"*{title}* [{level}]\n\n{message}"
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            resp = requests.post(url, json={"chat_id": self.chat_id, "text": text, "parse_mode": "Markdown"}, timeout=3)
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"Failed to send Telegram alert: {e}")
            return False
