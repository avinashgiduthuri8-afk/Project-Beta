"""Discord Webhook Notifier for Trading Bot Notifications."""

from __future__ import annotations

import logging
import requests
from typing import Optional
from core.interfaces import BaseNotifier

logger = logging.getLogger(__name__)


class DiscordNotifier(BaseNotifier):
    """Dispatches trade execution alerts and risk warnings to Discord channel webhook."""

    def __init__(self, webhook_url: Optional[str] = None, enabled: bool = False):
        self.webhook_url = webhook_url
        self.enabled = enabled and bool(webhook_url)

    def send_alert(self, title: str, message: str, level: str = "INFO") -> bool:
        if not self.enabled:
            logger.debug(f"[Discord Mock] {level} | {title}: {message}")
            return True

        # Color mapping: Green for SUCCESS/INFO, Red for ERROR/CRITICAL
        color_map = {
            "INFO": 3447003,      # Blue
            "SUCCESS": 3066993,   # Green
            "WARNING": 16776960,  # Yellow
            "ERROR": 15158332,    # Red
            "CRITICAL": 10038562  # Dark Red
        }

        embed = {
            "title": f"{title} [{level}]",
            "description": message,
            "color": color_map.get(level.upper(), 3447003),
        }

        try:
            resp = requests.post(self.webhook_url, json={"embeds": [embed]}, timeout=3)
            return resp.status_code in (200, 204)
        except Exception as e:
            logger.error(f"Failed to send Discord webhook: {e}")
            return False
