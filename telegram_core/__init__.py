"""
PROJECT ALPHA - Telegram Module
================================

Production Telegram bot integration with:
- Multi-bot support (Scanner, VGX, PMB, MTB)
- User authentication and rate limiting
- Trading/Risk/System notifications
- Interactive commands
- Dashboard integration
"""

from .production_bot import (
    ProductionTelegramBot,
    get_telegram_bot,
    get_notification_manager,
    start_telegram_bot,
    start_telegram_bot_sync,
    NotificationManager,
    NotificationType,
    TelegramConfig,
    SecurityManager,
    get_security_manager,
    require_auth,
    require_admin,
)

from .multi_bot_config import (
    MultiBotConfig,
    BotConfig,
    BotType,
    get_multi_bot_config,
    get_scanner_config,
    get_vgx_config,
    get_pmb_config,
    get_mtb_config,
    get_alerts_config,
)

__all__ = [
    # Production bot
    "ProductionTelegramBot",
    "get_telegram_bot",
    "get_notification_manager",
    "start_telegram_bot",
    "start_telegram_bot_sync",
    "NotificationManager",
    "NotificationType",
    "TelegramConfig",
    "SecurityManager",
    "get_security_manager",
    "require_auth",
    "require_admin",
    # Multi-bot config
    "MultiBotConfig",
    "BotConfig",
    "BotType",
    "get_multi_bot_config",
    "get_scanner_config",
    "get_vgx_config",
    "get_pmb_config",
    "get_mtb_config",
    "get_alerts_config",
]

__version__ = "1.1.0"
