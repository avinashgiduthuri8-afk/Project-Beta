"""
PROJECT ALPHA - Multi-Bot Telegram Configuration
================================================

Centralized configuration for multiple Telegram bots:
- Scanner Bot
- VGX Bot (Volatile Grid X)
- PMB Bot (Price Movement Bot)
- MTB Bot (MACD Trend Bounce)

Each bot uses dedicated environment variables for token and chat ID.
Backward compatible with legacy BOT_TOKEN variable.
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from typing import Dict, Optional, Set

logger = logging.getLogger("telegram.config")


# =============================================================================
# BOT IDENTIFIERS
# =============================================================================

class BotType:
    """Bot type identifiers."""
    SCANNER = "scanner"
    VGX = "vgx"
    PMB = "pmb"
    MTB = "mtb"
    ALERTS = "alerts"  # Global monitoring alerts


# =============================================================================
# BOT CONFIGURATION
# =============================================================================

@dataclass
class BotConfig:
    """Configuration for a single Telegram bot."""
    bot_type: str
    token: str
    chat_id: str
    enabled: bool = True
    
    @property
    def is_configured(self) -> bool:
        """Check if bot is properly configured."""
        return bool(self.token)
    
    @property
    def can_send_messages(self) -> bool:
        """Check if bot can send messages (has token and chat_id)."""
        return bool(self.token and self.chat_id)


# =============================================================================
# MULTI-BOT CONFIGURATION MANAGER
# =============================================================================

class MultiBotConfig:
    """
    Centralized configuration manager for multiple Telegram bots.
    
    Environment Variables:
    ----------------------
    # Scanner Bot
    SCANNER_BOT_TOKEN   - Telegram bot token for Scanner
    SCANNER_CHAT_ID     - Chat ID for Scanner notifications
    
    # Volatile Grid X Bot
    VGX_BOT_TOKEN       - Telegram bot token for VGX
    VGX_CHAT_ID         - Chat ID for VGX notifications
    
    # Price Movement Bot
    PMB_BOT_TOKEN       - Telegram bot token for PMB
    PMB_CHAT_ID         - Chat ID for PMB notifications
    
    # MACD Trend Bounce Bot
    MTB_BOT_TOKEN       - Telegram bot token for MTB
    MTB_CHAT_ID         - Chat ID for MTB notifications
    
    # Global Admin (shared across all bots)
    TELEGRAM_ADMIN_IDS  - Comma-separated admin user IDs
    TELEGRAM_ALLOWED_IDS - Comma-separated allowed user IDs
    
    # Legacy (fallback, deprecated)
    BOT_TOKEN           - Legacy fallback token (use bot-specific vars)
    TELEGRAM_CHAT_ID    - Legacy fallback chat ID
    """
    
    _instance: Optional["MultiBotConfig"] = None
    
    def __new__(cls) -> "MultiBotConfig":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._bots: Dict[str, BotConfig] = {}
        self._load_configurations()
        self._initialized = True
    
    def _load_configurations(self) -> None:
        """Load all bot configurations from environment variables."""
        
        # Legacy fallback token (deprecated but supported)
        # ENV: BOT_TOKEN - Legacy fallback, prefer bot-specific tokens
        legacy_token = os.getenv("BOT_TOKEN", "")
        # ENV: TELEGRAM_CHAT_ID - Legacy fallback chat ID
        legacy_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        
        # Scanner Bot Configuration
        # ENV: SCANNER_BOT_TOKEN - Telegram bot token for Scanner module
        # ENV: SCANNER_CHAT_ID - Chat ID for Scanner notifications
        self._bots[BotType.SCANNER] = BotConfig(
            bot_type=BotType.SCANNER,
            token=os.getenv("SCANNER_BOT_TOKEN", "") or legacy_token,
            chat_id=os.getenv("SCANNER_CHAT_ID", "") or legacy_chat_id,
        )
        
        # VGX Bot Configuration
        # ENV: VGX_BOT_TOKEN - Telegram bot token for Volatile Grid X
        # ENV: VGX_CHAT_ID - Chat ID for VGX notifications
        self._bots[BotType.VGX] = BotConfig(
            bot_type=BotType.VGX,
            token=os.getenv("VGX_BOT_TOKEN", "") or legacy_token,
            chat_id=os.getenv("VGX_CHAT_ID", "") or legacy_chat_id,
        )
        
        # PMB Bot Configuration
        # ENV: PMB_BOT_TOKEN - Telegram bot token for Price Movement Bot
        # ENV: PMB_CHAT_ID - Chat ID for PMB notifications
        self._bots[BotType.PMB] = BotConfig(
            bot_type=BotType.PMB,
            token=os.getenv("PMB_BOT_TOKEN", "") or legacy_token,
            chat_id=os.getenv("PMB_CHAT_ID", "") or legacy_chat_id,
        )
        
        # MTB Bot Configuration
        # ENV: MTB_BOT_TOKEN - Telegram bot token for MACD Trend Bounce
        # ENV: MTB_CHAT_ID - Chat ID for MTB notifications
        self._bots[BotType.MTB] = BotConfig(
            bot_type=BotType.MTB,
            token=os.getenv("MTB_BOT_TOKEN", "") or legacy_token,
            chat_id=os.getenv("MTB_CHAT_ID", "") or legacy_chat_id,
        )
        
        # Alerts Bot Configuration (for monitoring/system alerts)
        # ENV: ALERT_BOT_TOKEN - Telegram bot token for system alerts
        # ENV: ALERT_CHAT_ID - Chat ID for system alerts
        self._bots[BotType.ALERTS] = BotConfig(
            bot_type=BotType.ALERTS,
            token=os.getenv("ALERT_BOT_TOKEN", "") or legacy_token,
            chat_id=os.getenv("ALERT_CHAT_ID", "") or legacy_chat_id,
        )
        
        # Log configuration status
        for bot_type, config in self._bots.items():
            if config.is_configured:
                logger.info("[%s] Bot configured (chat_id=%s)", 
                           bot_type.upper(), "YES" if config.chat_id else "NO")
            else:
                logger.warning("[%s] Bot NOT configured (missing token)", bot_type.upper())
    
    def get_config(self, bot_type: str) -> BotConfig:
        """Get configuration for a specific bot."""
        return self._bots.get(bot_type, BotConfig(bot_type=bot_type, token="", chat_id=""))
    
    def get_token(self, bot_type: str) -> str:
        """Get token for a specific bot."""
        return self.get_config(bot_type).token
    
    def get_chat_id(self, bot_type: str) -> str:
        """Get chat ID for a specific bot."""
        return self.get_config(bot_type).chat_id
    
    def is_configured(self, bot_type: str) -> bool:
        """Check if a specific bot is configured."""
        return self.get_config(bot_type).is_configured
    
    def get_configured_bots(self) -> Dict[str, BotConfig]:
        """Get all configured bots."""
        return {k: v for k, v in self._bots.items() if v.is_configured}
    
    def get_all_configs(self) -> Dict[str, BotConfig]:
        """Get all bot configurations."""
        return dict(self._bots)
    
    @staticmethod
    def get_admin_ids() -> Set[int]:
        """
        Get admin user IDs (shared across all bots).
        ENV: TELEGRAM_ADMIN_IDS - Comma-separated admin user IDs
        """
        admin_str = os.getenv("TELEGRAM_ADMIN_IDS", "")
        return set(
            int(uid.strip())
            for uid in admin_str.split(",")
            if uid.strip().isdigit()
        )
    
    @staticmethod
    def get_allowed_ids() -> Set[int]:
        """
        Get allowed user IDs (shared across all bots).
        ENV: TELEGRAM_ALLOWED_IDS - Comma-separated allowed user IDs
        """
        allowed_str = os.getenv("TELEGRAM_ALLOWED_IDS", "")
        admin_ids = MultiBotConfig.get_admin_ids()
        
        allowed = set(
            int(uid.strip())
            for uid in allowed_str.split(",")
            if uid.strip().isdigit()
        )
        # Admins are always allowed
        allowed.update(admin_ids)
        return allowed
    
    def reload(self) -> None:
        """Reload all configurations from environment."""
        self._bots.clear()
        self._load_configurations()
        logger.info("MultiBotConfig reloaded")
    
    def status_summary(self) -> Dict[str, str]:
        """Get status summary for all bots."""
        return {
            bot_type: "CONFIGURED" if config.is_configured else "NOT_CONFIGURED"
            for bot_type, config in self._bots.items()
        }


# =============================================================================
# SINGLETON ACCESSOR
# =============================================================================

_config_instance: Optional[MultiBotConfig] = None


def get_multi_bot_config() -> MultiBotConfig:
    """Get the singleton MultiBotConfig instance."""
    global _config_instance
    if _config_instance is None:
        _config_instance = MultiBotConfig()
    return _config_instance


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def get_scanner_config() -> BotConfig:
    """Get Scanner bot configuration."""
    return get_multi_bot_config().get_config(BotType.SCANNER)


def get_vgx_config() -> BotConfig:
    """Get VGX bot configuration."""
    return get_multi_bot_config().get_config(BotType.VGX)


def get_pmb_config() -> BotConfig:
    """Get PMB bot configuration."""
    return get_multi_bot_config().get_config(BotType.PMB)


def get_mtb_config() -> BotConfig:
    """Get MTB bot configuration."""
    return get_multi_bot_config().get_config(BotType.MTB)


def get_alerts_config() -> BotConfig:
    """Get Alerts bot configuration."""
    return get_multi_bot_config().get_config(BotType.ALERTS)
