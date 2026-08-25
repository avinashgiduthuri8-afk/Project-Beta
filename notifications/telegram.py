"""
Telegram Bot Notifier for Execution Alerts.
Sends markdown-formatted alerts for order placements, fills, SL hits, and circuit breakers.
"""

from __future__ import annotations

import logging
from typing import Optional
import requests
from config.config_loader import TelegramConfig
from core.interfaces import BaseNotifier
from core.models import Order

logger = logging.getLogger(__name__)


class TelegramNotifier(BaseNotifier):
    """Sends real-time execution alerts via Telegram Bot API."""

    def __init__(self, config: TelegramConfig) -> None:
        self.config = config
        self.bot_token = config.bot_token
        self.chat_id = config.chat_id
        self.enabled = config.enabled and bool(self.bot_token and self.chat_id)

    def send_message(self, text: str) -> bool:
        if not self.enabled:
            return False
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }
        try:
            resp = requests.post(url, json=payload, timeout=5)
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False

    def send_order_alert(self, order: Order) -> bool:
        if not self.enabled or not self.config.notify_on_order:
            return False
        emoji = "🟢" if order.side.value == "BUY" else "🔴"
        status_emoji = "✅" if order.status.value == "COMPLETE" else "⏳"

        msg = (
            f"{emoji} *Order Update: {order.symbol}*\n"
            f"• *Side*: {order.side.value} | *Product*: {order.product.value}\n"
            f"• *Qty*: {order.quantity} | *Price*: ₹{order.price or 'MKT'}\n"
            f"• *Status*: {status_emoji} `{order.status.value}`\n"
            f"• *Order ID*: `{order.order_id}`\n"
        )
        if order.status_message:
            msg += f"• *Info*: _{order.status_message}_\n"
        return self.send_message(msg)

    def send_rms_alert(self, message: str) -> bool:
        if not self.enabled or not self.config.notify_on_rms_trigger:
            return False
        msg = f"🚨 *RISK MANAGEMENT ALERT*\n\n{message}"
        return self.send_message(msg)
