"""
Discord Webhook Notifier for Execution Alerts.
Sends rich embed messages for trade updates and risk triggers.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional
import requests
from config.config_loader import DiscordConfig
from core.interfaces import BaseNotifier
from core.models import Order

logger = logging.getLogger(__name__)


class DiscordNotifier(BaseNotifier):
    """Sends webhook alerts to Discord channels."""

    def __init__(self, config: DiscordConfig) -> None:
        self.config = config
        self.webhook_url = config.webhook_url
        self.enabled = config.enabled and bool(self.webhook_url)

    def send_message(self, text: str) -> bool:
        if not self.enabled or not self.webhook_url:
            return False
        payload = {"content": text}
        try:
            resp = requests.post(self.webhook_url, json=payload, timeout=5)
            return resp.status_code in (200, 204)
        except Exception as e:
            logger.error(f"Failed to post to Discord webhook: {e}")
            return False

    def send_order_alert(self, order: Order) -> bool:
        if not self.enabled or not self.config.notify_on_order or not self.webhook_url:
            return False
        color = 3066993 if order.side.value == "BUY" else 15158332  # Green vs Red

        embed: Dict[str, Any] = {
            "title": f"Order Alert: {order.symbol} ({order.side.value})",
            "color": color,
            "fields": [
                {"name": "Status", "value": f"`{order.status.value}`", "inline": True},
                {"name": "Product", "value": order.product.value, "inline": True},
                {"name": "Quantity", "value": str(order.quantity), "inline": True},
                {"name": "Price", "value": f"₹{order.price or 'MKT'}", "inline": True},
                {"name": "Order ID", "value": order.order_id, "inline": False},
            ],
        }
        if order.status_message:
            embed["fields"].append({"name": "Message", "value": order.status_message, "inline": False})

        payload = {"embeds": [embed]}
        try:
            resp = requests.post(self.webhook_url, json=payload, timeout=5)
            return resp.status_code in (200, 204)
        except Exception as e:
            logger.error(f"Failed to send Discord embed: {e}")
            return False

    def send_rms_alert(self, message: str) -> bool:
        if not self.enabled or not self.config.notify_on_rms_trigger or not self.webhook_url:
            return False
        embed = {
            "title": "🚨 Risk Management Alert",
            "description": message,
            "color": 15158332,  # Red
        }
        payload = {"embeds": [embed]}
        try:
            resp = requests.post(self.webhook_url, json=payload, timeout=5)
            return resp.status_code in (200, 204)
        except Exception as e:
            logger.error(f"Failed to send Discord RMS embed: {e}")
            return False
