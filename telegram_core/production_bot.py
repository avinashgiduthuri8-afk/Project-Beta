"""
PROJECT ALPHA - Production Telegram Bot Integration
====================================================

Comprehensive Telegram bot integration for:
- Trading notifications (open, close, TP, SL, trailing)
- Risk notifications (loss limits, circuit breaker, drawdown)
- System notifications (startup, health, storage, CPU/memory)
- Interactive commands (status, health, positions, signals)
- User authentication with admin/allowed roles
- Rate limiting and security logging

Thread-safe implementation with automatic reconnection.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

# Telegram imports
try:
    from telegram import Update, Bot, BotCommand
    from telegram.ext import (
        ApplicationBuilder,
        CommandHandler,
        ContextTypes,
        MessageHandler,
        filters,
    )
    from telegram.error import TelegramError, NetworkError, TimedOut, RetryAfter
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    Update = None
    Bot = None

logger = logging.getLogger("telegram.production")


# =============================================================================
# CONFIGURATION
# =============================================================================

class TelegramConfig:
    """
    Centralized Telegram configuration.
    
    Multi-bot configuration - V1
    This class provides backward-compatible access to Telegram settings.
    For multi-bot configurations, use telegram.multi_bot_config module.
    
    Environment Variables:
    ----------------------
    # Global Admin (shared across all bots)
    # ENV: TELEGRAM_ADMIN_IDS - Comma-separated admin user IDs
    # ENV: TELEGRAM_ALLOWED_IDS - Comma-separated allowed user IDs
    
    # Legacy variables (deprecated, use bot-specific vars)
    # ENV: BOT_TOKEN - Legacy fallback token
    # ENV: TELEGRAM_CHAT_ID - Legacy fallback chat ID
    """
    
    # Multi-bot configuration - V1
    # ENV: BOT_TOKEN - Legacy fallback token (prefer bot-specific tokens)
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    
    # ENV: TELEGRAM_CHAT_ID - Legacy fallback chat ID (prefer bot-specific)
    NOTIFICATION_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", os.getenv("NOTIFICATION_CHAT_ID", ""))
    
    # ENV: TELEGRAM_ADMIN_IDS - Comma-separated admin user IDs (shared across all bots)
    ADMIN_IDS: Set[int] = set(
        int(uid.strip())
        for uid in os.getenv("TELEGRAM_ADMIN_IDS", "").split(",")
        if uid.strip().isdigit()
    )
    
    # ENV: TELEGRAM_ALLOWED_IDS - Comma-separated allowed user IDs (shared across all bots)
    ALLOWED_IDS: Set[int] = set(
        int(uid.strip())
        for uid in os.getenv("TELEGRAM_ALLOWED_IDS", "").split(",")
        if uid.strip().isdigit()
    )
    
    # Include admins in allowed
    ALLOWED_IDS.update(ADMIN_IDS)
    
    # Security settings
    SECURITY_ENABLED: bool = os.getenv("TELEGRAM_SECURITY_ENABLED", "true").lower() == "true"
    RATE_LIMIT_WINDOW: int = int(os.getenv("RATE_LIMIT_WINDOW", "60"))
    RATE_LIMIT_MAX: int = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "30"))
    
    # Notification settings
    NOTIFICATIONS_ENABLED: bool = os.getenv("TELEGRAM_NOTIFICATIONS", "true").lower() == "true"
    TRADE_NOTIFICATIONS: bool = os.getenv("TELEGRAM_TRADE_NOTIFICATIONS", "true").lower() == "true"
    RISK_NOTIFICATIONS: bool = os.getenv("TELEGRAM_RISK_NOTIFICATIONS", "true").lower() == "true"
    SYSTEM_NOTIFICATIONS: bool = os.getenv("TELEGRAM_SYSTEM_NOTIFICATIONS", "true").lower() == "true"
    
    # Retry settings
    MAX_RETRIES: int = 5
    RETRY_DELAY: int = 5
    RECONNECT_DELAY: int = 30
    
    @classmethod
    def is_configured(cls) -> bool:
        """Check if Telegram is properly configured."""
        return bool(cls.BOT_TOKEN)
    
    @classmethod
    def reload(cls) -> None:
        """Reload configuration from environment."""
        cls.BOT_TOKEN = os.getenv("BOT_TOKEN", "")
        cls.NOTIFICATION_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", os.getenv("NOTIFICATION_CHAT_ID", ""))
        
        cls.ADMIN_IDS = set(
            int(uid.strip())
            for uid in os.getenv("TELEGRAM_ADMIN_IDS", "").split(",")
            if uid.strip().isdigit()
        )
        
        cls.ALLOWED_IDS = set(
            int(uid.strip())
            for uid in os.getenv("TELEGRAM_ALLOWED_IDS", "").split(",")
            if uid.strip().isdigit()
        )
        cls.ALLOWED_IDS.update(cls.ADMIN_IDS)


# =============================================================================
# NOTIFICATION TYPES
# =============================================================================

class NotificationType(Enum):
    # Trading
    TRADE_OPENED = "trade_opened"
    TRADE_CLOSED = "trade_closed"
    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"
    TRADE_REJECTED = "trade_rejected"
    PARTIAL_PROFIT = "partial_profit"
    TRAILING_ACTIVATED = "trailing_activated"
    EMERGENCY_CLOSE = "emergency_close"
    
    # Risk
    DAILY_LOSS_LIMIT = "daily_loss_limit"
    WEEKLY_LOSS_LIMIT = "weekly_loss_limit"
    MONTHLY_LOSS_LIMIT = "monthly_loss_limit"
    CIRCUIT_BREAKER_ON = "circuit_breaker_on"
    CIRCUIT_BREAKER_OFF = "circuit_breaker_off"
    EMERGENCY_STOP = "emergency_stop"
    MAX_DRAWDOWN = "max_drawdown"
    
    # System
    BOT_STARTED = "bot_started"
    BOT_RESTARTED = "bot_restarted"
    BOT_STOPPED = "bot_stopped"
    SCANNER_CONNECTED = "scanner_connected"
    SCANNER_DISCONNECTED = "scanner_disconnected"
    API_UNAVAILABLE = "api_unavailable"
    STORAGE_CORRUPTION = "storage_corruption"
    BACKUP_RESTORED = "backup_restored"
    MONITORING_FAILURE = "monitoring_failure"
    HIGH_CPU = "high_cpu"
    HIGH_MEMORY = "high_memory"


NOTIFICATION_EMOJI = {
    NotificationType.TRADE_OPENED: "🟢",
    NotificationType.TRADE_CLOSED: "🔵",
    NotificationType.TAKE_PROFIT: "🎯",
    NotificationType.STOP_LOSS: "🛑",
    NotificationType.TRADE_REJECTED: "❌",
    NotificationType.PARTIAL_PROFIT: "💰",
    NotificationType.TRAILING_ACTIVATED: "📈",
    NotificationType.EMERGENCY_CLOSE: "🚨",
    NotificationType.DAILY_LOSS_LIMIT: "⚠️",
    NotificationType.WEEKLY_LOSS_LIMIT: "⚠️",
    NotificationType.MONTHLY_LOSS_LIMIT: "⚠️",
    NotificationType.CIRCUIT_BREAKER_ON: "🔴",
    NotificationType.CIRCUIT_BREAKER_OFF: "🟢",
    NotificationType.EMERGENCY_STOP: "🚨",
    NotificationType.MAX_DRAWDOWN: "📉",
    NotificationType.BOT_STARTED: "🚀",
    NotificationType.BOT_RESTARTED: "🔄",
    NotificationType.BOT_STOPPED: "⏹️",
    NotificationType.SCANNER_CONNECTED: "📡",
    NotificationType.SCANNER_DISCONNECTED: "📴",
    NotificationType.API_UNAVAILABLE: "🌐",
    NotificationType.STORAGE_CORRUPTION: "💾",
    NotificationType.BACKUP_RESTORED: "📦",
    NotificationType.MONITORING_FAILURE: "🔧",
    NotificationType.HIGH_CPU: "🔥",
    NotificationType.HIGH_MEMORY: "💾",
}


# =============================================================================
# SECURITY & RATE LIMITING
# =============================================================================

@dataclass
class RateLimitEntry:
    """Rate limit tracking per user."""
    window_start: float
    request_count: int


class SecurityManager:
    """Manages authentication and rate limiting."""
    
    def __init__(self):
        self._rate_limits: Dict[int, RateLimitEntry] = {}
        self._lock = threading.Lock()
        self._denied_attempts: deque = deque(maxlen=1000)
        self._security_events: deque = deque(maxlen=500)
    
    def is_admin(self, user_id: int) -> bool:
        """Check if user is admin."""
        if not TelegramConfig.SECURITY_ENABLED:
            return True
        return user_id in TelegramConfig.ADMIN_IDS
    
    def is_allowed(self, user_id: int) -> bool:
        """Check if user is allowed."""
        if not TelegramConfig.SECURITY_ENABLED:
            return True
        if not TelegramConfig.ALLOWED_IDS:
            return True  # No whitelist = allow all
        return user_id in TelegramConfig.ALLOWED_IDS
    
    def check_rate_limit(self, user_id: int) -> Tuple[bool, int, int]:
        """Check rate limit. Returns (allowed, remaining, reset_in)."""
        now = time.time()
        
        with self._lock:
            if user_id not in self._rate_limits:
                self._rate_limits[user_id] = RateLimitEntry(now, 1)
                return True, TelegramConfig.RATE_LIMIT_MAX - 1, TelegramConfig.RATE_LIMIT_WINDOW
            
            entry = self._rate_limits[user_id]
            
            if now - entry.window_start > TelegramConfig.RATE_LIMIT_WINDOW:
                self._rate_limits[user_id] = RateLimitEntry(now, 1)
                return True, TelegramConfig.RATE_LIMIT_MAX - 1, TelegramConfig.RATE_LIMIT_WINDOW
            
            if entry.request_count >= TelegramConfig.RATE_LIMIT_MAX:
                reset_in = int(TelegramConfig.RATE_LIMIT_WINDOW - (now - entry.window_start))
                return False, 0, reset_in
            
            entry.request_count += 1
            remaining = TelegramConfig.RATE_LIMIT_MAX - entry.request_count
            reset_in = int(TelegramConfig.RATE_LIMIT_WINDOW - (now - entry.window_start))
            return True, remaining, reset_in
    
    def log_denied_access(self, user_id: int, username: Optional[str], command: str, reason: str) -> None:
        """Log denied access attempt."""
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user_id": user_id,
            "username": username,
            "command": command,
            "reason": reason,
        }
        self._denied_attempts.append(event)
        self._security_events.append(event)
        
        logger.warning(
            "SECURITY: Access denied - User %s (%s) - Command: %s - Reason: %s",
            user_id, username, command, reason
        )
        
        # Report to monitoring if available
        try:
            from monitoring import get_metrics_collector
            collector = get_metrics_collector()
            collector.record_security_event(
                event_type="access_denied",
                user_id=str(user_id),
                details=f"Command: {command}, Reason: {reason}",
                success=False,
            )
        except Exception:
            pass
    
    def get_denied_attempts(self, limit: int = 50) -> List[Dict]:
        """Get recent denied attempts."""
        return list(self._denied_attempts)[-limit:]
    
    def get_security_events(self, limit: int = 100) -> List[Dict]:
        """Get recent security events."""
        return list(self._security_events)[-limit:]


# Global security manager
_security_manager: Optional[SecurityManager] = None


def get_security_manager() -> SecurityManager:
    """Get security manager instance."""
    global _security_manager
    if _security_manager is None:
        _security_manager = SecurityManager()
    return _security_manager


# =============================================================================
# DECORATORS
# =============================================================================

def require_auth(func: Callable) -> Callable:
    """Decorator to require authentication."""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if not update.effective_user:
            return
        
        user_id = update.effective_user.id
        username = update.effective_user.username
        command = update.message.text if update.message else "unknown"
        
        security = get_security_manager()
        
        # Check allowed
        if not security.is_allowed(user_id):
            security.log_denied_access(user_id, username, command, "Not in allowed list")
            await update.message.reply_text(
                "⛔ *Access Denied*\n\nYou are not authorized to use this bot.\n"
                "Contact an administrator for access.",
                parse_mode="Markdown"
            )
            return
        
        # Check rate limit
        allowed, remaining, reset_in = security.check_rate_limit(user_id)
        if not allowed:
            security.log_denied_access(user_id, username, command, "Rate limit exceeded")
            await update.message.reply_text(
                f"⏳ *Rate Limit Exceeded*\n\nPlease wait {reset_in} seconds before trying again.",
                parse_mode="Markdown"
            )
            return
        
        return await func(update, context, *args, **kwargs)
    return wrapper


def require_admin(func: Callable) -> Callable:
    """Decorator to require admin privileges."""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if not update.effective_user:
            return
        
        user_id = update.effective_user.id
        username = update.effective_user.username
        command = update.message.text if update.message else "unknown"
        
        security = get_security_manager()
        
        if not security.is_admin(user_id):
            security.log_denied_access(user_id, username, command, "Admin required")
            await update.message.reply_text(
                "⛔ *Admin Required*\n\nThis command requires administrator privileges.",
                parse_mode="Markdown"
            )
            return
        
        return await func(update, context, *args, **kwargs)
    return wrapper


# =============================================================================
# NOTIFICATION MANAGER
# =============================================================================

class NotificationManager:
    """Manages sending notifications with de-duplication."""
    
    def __init__(self, bot: Optional[Bot] = None):
        self._bot = bot
        self._lock = threading.Lock()
        self._sent_notifications: Dict[str, datetime] = {}
        self._notification_queue: deque = deque(maxlen=100)
        self._cooldowns: Dict[str, int] = {
            NotificationType.TRADE_OPENED.value: 0,
            NotificationType.TRADE_CLOSED.value: 0,
            NotificationType.TAKE_PROFIT.value: 0,
            NotificationType.STOP_LOSS.value: 0,
            NotificationType.CIRCUIT_BREAKER_ON.value: 300,  # 5 min
            NotificationType.HIGH_CPU.value: 300,
            NotificationType.HIGH_MEMORY.value: 300,
        }
    
    def set_bot(self, bot: Bot) -> None:
        """Set bot instance."""
        self._bot = bot
    
    def _is_duplicate(self, notif_type: NotificationType, details: str) -> bool:
        """Check if notification was recently sent."""
        key = f"{notif_type.value}:{details}"
        cooldown = self._cooldowns.get(notif_type.value, 60)
        
        if cooldown == 0:
            return False
        
        with self._lock:
            last_sent = self._sent_notifications.get(key)
            if last_sent:
                age = (datetime.now(timezone.utc) - last_sent).total_seconds()
                if age < cooldown:
                    return True
            return False
    
    def _mark_sent(self, notif_type: NotificationType, details: str) -> None:
        """Mark notification as sent."""
        key = f"{notif_type.value}:{details}"
        with self._lock:
            self._sent_notifications[key] = datetime.now(timezone.utc)
            
            # Cleanup old entries
            cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
            self._sent_notifications = {
                k: v for k, v in self._sent_notifications.items()
                if v > cutoff
            }
    
    async def send_notification(
        self,
        notif_type: NotificationType,
        title: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
        chat_id: Optional[str] = None,
    ) -> bool:
        """Send a notification."""
        if not TelegramConfig.NOTIFICATIONS_ENABLED:
            return False
        
        # Check category enabled
        if notif_type.value.startswith("trade_") and not TelegramConfig.TRADE_NOTIFICATIONS:
            return False
        if notif_type.value in ["daily_loss_limit", "weekly_loss_limit", "monthly_loss_limit", 
                                 "circuit_breaker_on", "circuit_breaker_off", "emergency_stop", "max_drawdown"]:
            if not TelegramConfig.RISK_NOTIFICATIONS:
                return False
        if notif_type.value in ["bot_started", "bot_restarted", "bot_stopped", "scanner_connected",
                                 "scanner_disconnected", "api_unavailable", "storage_corruption",
                                 "backup_restored", "monitoring_failure", "high_cpu", "high_memory"]:
            if not TelegramConfig.SYSTEM_NOTIFICATIONS:
                return False
        
        # Check duplicate
        details_str = json.dumps(details or {}, sort_keys=True)
        if self._is_duplicate(notif_type, details_str):
            logger.debug("Duplicate notification suppressed: %s", notif_type.value)
            return False
        
        # Build message
        emoji = NOTIFICATION_EMOJI.get(notif_type, "📢")
        text = f"{emoji} *{title}*\n\n{message}"
        
        if details:
            text += "\n\n*Details:*"
            for key, value in details.items():
                text += f"\n• {key}: `{value}`"
        
        text += f"\n\n_Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}_"
        
        # Send
        target_chat = chat_id or TelegramConfig.NOTIFICATION_CHAT_ID
        if not target_chat:
            logger.warning("No chat ID configured for notifications")
            return False
        
        if not self._bot:
            logger.warning("Bot not initialized for notifications")
            return False
        
        try:
            await self._bot.send_message(
                chat_id=target_chat,
                text=text,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
            self._mark_sent(notif_type, details_str)
            logger.info("Notification sent: %s", notif_type.value)
            return True
        except Exception as e:
            logger.error("Failed to send notification: %s", e)
            return False
    
    # Convenience methods
    async def trade_opened(self, coin: str, qty: float, price: float, source: str = "") -> bool:
        return await self.send_notification(
            NotificationType.TRADE_OPENED,
            "Trade Opened",
            f"Position opened for *{coin}*",
            {"Coin": coin, "Quantity": qty, "Price": f"₹{price:,.2f}", "Source": source or "Scanner"}
        )
    
    async def trade_closed(self, coin: str, pnl: float, pnl_pct: float, reason: str = "") -> bool:
        emoji = "📈" if pnl > 0 else "📉"
        return await self.send_notification(
            NotificationType.TRADE_CLOSED,
            f"Trade Closed {emoji}",
            f"Position closed for *{coin}*",
            {"Coin": coin, "PnL": f"₹{pnl:,.2f}", "PnL %": f"{pnl_pct:+.2f}%", "Reason": reason or "Manual"}
        )
    
    async def take_profit_hit(self, coin: str, pnl: float, target_pct: float) -> bool:
        return await self.send_notification(
            NotificationType.TAKE_PROFIT,
            "Take Profit Hit! 🎯",
            f"Target reached for *{coin}*",
            {"Coin": coin, "PnL": f"₹{pnl:,.2f}", "Target": f"{target_pct}%"}
        )
    
    async def stop_loss_hit(self, coin: str, loss: float, stop_pct: float) -> bool:
        return await self.send_notification(
            NotificationType.STOP_LOSS,
            "Stop Loss Triggered 🛑",
            f"Stop loss hit for *{coin}*",
            {"Coin": coin, "Loss": f"₹{loss:,.2f}", "Stop Level": f"{stop_pct}%"}
        )
    
    async def trade_rejected(self, coin: str, reason: str) -> bool:
        return await self.send_notification(
            NotificationType.TRADE_REJECTED,
            "Trade Rejected",
            f"Trade rejected for *{coin}*",
            {"Coin": coin, "Reason": reason}
        )
    
    async def trailing_activated(self, coin: str, current_profit: float) -> bool:
        return await self.send_notification(
            NotificationType.TRAILING_ACTIVATED,
            "Trailing Stop Activated",
            f"Trailing stop now active for *{coin}*",
            {"Coin": coin, "Current Profit": f"{current_profit:+.2f}%"}
        )
    
    async def circuit_breaker_activated(self, reason: str, loss_pct: float) -> bool:
        return await self.send_notification(
            NotificationType.CIRCUIT_BREAKER_ON,
            "Circuit Breaker ACTIVATED 🚨",
            "Trading has been halted due to loss limits.",
            {"Reason": reason, "Loss": f"{loss_pct:.2f}%"}
        )
    
    async def circuit_breaker_reset(self) -> bool:
        return await self.send_notification(
            NotificationType.CIRCUIT_BREAKER_OFF,
            "Circuit Breaker Reset ✅",
            "Trading has resumed.",
        )
    
    async def bot_started(self) -> bool:
        return await self.send_notification(
            NotificationType.BOT_STARTED,
            "Bot Started 🚀",
            "PROJECT ALPHA trading bot is now online.",
            {"Version": "1.0", "Mode": os.getenv("VGX_BOT_MODE", "PAPER")}
        )
    
    async def high_cpu_alert(self, cpu_pct: float) -> bool:
        return await self.send_notification(
            NotificationType.HIGH_CPU,
            "High CPU Usage",
            f"CPU usage is critically high at *{cpu_pct:.1f}%*",
            {"CPU": f"{cpu_pct:.1f}%", "Threshold": "85%"}
        )
    
    async def high_memory_alert(self, mem_pct: float) -> bool:
        return await self.send_notification(
            NotificationType.HIGH_MEMORY,
            "High Memory Usage",
            f"Memory usage is critically high at *{mem_pct:.1f}%*",
            {"Memory": f"{mem_pct:.1f}%", "Threshold": "85%"}
        )


# =============================================================================
# COMMAND HANDLERS
# =============================================================================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    user = update.effective_user
    await update.message.reply_text(
        f"👋 *Welcome to PROJECT ALPHA Bot!*\n\n"
        f"Hello, {user.first_name}!\n\n"
        f"Use /help to see available commands.",
        parse_mode="Markdown"
    )


@require_auth
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    security = get_security_manager()
    is_admin = security.is_admin(update.effective_user.id)
    
    commands = """
📋 *Available Commands*

*General:*
/start - Start the bot
/help - Show this help
/ping - Check bot status
/version - Show version info

*Status:*
/status - System status overview
/health - Health check results
/dashboard - Dashboard summary

*Trading:*
/pnl - Profit/Loss summary
/stats - Trading statistics
/positions - Open positions
/portfolio - Portfolio overview

*Signals:*
/signals - Active trading signals
/watchlist - Current watchlist

*Risk:*
/risk - Risk engine status
"""
    
    if is_admin:
        commands += """
*Admin Commands:*
/pause - Pause trading
/resume - Resume trading
/emergency - Emergency stop
/restart - Restart services
/logs - View recent logs
"""
    
    await update.message.reply_text(commands, parse_mode="Markdown")


@require_auth
async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /ping command."""
    start = time.time()
    await update.message.reply_text("🏓 Pong!")
    latency = (time.time() - start) * 1000
    await update.message.reply_text(f"Response time: `{latency:.0f}ms`", parse_mode="Markdown")


@require_auth
async def cmd_version(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /version command."""
    await update.message.reply_text(
        "📦 *PROJECT ALPHA*\n\n"
        f"Version: `1.0.0`\n"
        f"Mode: `{os.getenv('VGX_BOT_MODE', 'PAPER')}`\n"
        f"Environment: `{os.getenv('RAILWAY_ENVIRONMENT', 'DEVELOPMENT')}`",
        parse_mode="Markdown"
    )


@require_auth
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status command."""
    try:
        # Get data from various sources
        from bots.mtb_bot.storage import snapshot as mtb_snapshot
        from bots.pmb_bot.storage import snapshot as pmb_snapshot
        from bots.risk_engine.engine import snapshot as risk_snapshot
        from bots.scanner_bot.scanner import get_stats
        
        mtb = mtb_snapshot()
        pmb = pmb_snapshot()
        risk = risk_snapshot()
        scanner_stats = get_stats() or {}
        
        # Determine statuses
        trading_enabled = os.getenv("TRADING_ENABLED", "true").lower() == "true"
        emergency_stop = os.getenv("EMERGENCY_STOP", "false").lower() == "true"
        
        if emergency_stop:
            status = "🚨 EMERGENCY STOP"
        elif not trading_enabled:
            status = "⏸️ PAUSED"
        else:
            status = "🟢 ACTIVE"
        
        message = (
            f"📊 *System Status*\n\n"
            f"*Trading Status:* {status}\n\n"
            f"*Services:*\n"
            f"├ Scanner: `{scanner_stats.get('api_status', 'ONLINE')}`\n"
            f"├ MTB Bot: `{mtb.get('status', 'UNKNOWN')}`\n"
            f"├ PMB Bot: `{pmb.get('status', 'UNKNOWN')}`\n"
            f"└ Risk Engine: `{'ACTIVE' if not emergency_stop else 'EMERGENCY'}`\n\n"
            f"*Risk Status:*\n"
            f"├ Kill Switch: `{'ON' if risk.get('kill_switch_active') else 'OFF'}`\n"
            f"├ Emergency Stop: `{'ON' if risk.get('emergency_stop') else 'OFF'}`\n"
            f"└ Circuit Breaker: `{risk.get('circuit_breaker_status', 'CLOSED')}`"
        )
        
        await update.message.reply_text(message, parse_mode="Markdown")
    except Exception as e:
        logger.error("Status command error: %s", e)
        await update.message.reply_text(f"❌ Error fetching status: {str(e)}")


@require_auth
async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /health command."""
    try:
        from monitoring import get_health_checker
        
        checker = get_health_checker()
        report = checker.run_all_checks()
        
        status_emoji = {
            "HEALTHY": "🟢",
            "DEGRADED": "🟡",
            "UNHEALTHY": "🔴",
            "UNKNOWN": "⚪",
        }
        
        emoji = status_emoji.get(report.overall_status.value, "❓")
        
        message = (
            f"{emoji} *Health Check Report*\n\n"
            f"*Overall Status:* `{report.overall_status.value}`\n\n"
            f"*Summary:*\n"
            f"├ Healthy: `{report.summary['healthy']}`\n"
            f"├ Degraded: `{report.summary['degraded']}`\n"
            f"├ Unhealthy: `{report.summary['unhealthy']}`\n"
            f"└ Total Checks: `{report.summary['total']}`\n"
        )
        
        if report.critical_issues:
            message += f"\n*Critical Issues:*\n"
            for issue in report.critical_issues[:5]:
                message += f"• {issue}\n"
        
        if report.warnings:
            message += f"\n*Warnings:*\n"
            for warning in report.warnings[:3]:
                message += f"• {warning}\n"
        
        await update.message.reply_text(message, parse_mode="Markdown")
    except Exception as e:
        logger.error("Health command error: %s", e)
        await update.message.reply_text(f"❌ Error: {str(e)}")


@require_auth
async def cmd_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /dashboard command."""
    try:
        from monitoring import MonitoringDashboard
        
        dashboard = MonitoringDashboard()
        summary = dashboard.get_dashboard_summary()
        
        status_emoji = {
            "HEALTHY": "🟢",
            "DEGRADED": "🟡",
            "UNHEALTHY": "🔴",
        }
        
        emoji = status_emoji.get(summary.get("overall_health", ""), "❓")
        
        message = (
            f"{emoji} *Dashboard Summary*\n\n"
            f"*System Health:* `{summary.get('overall_health', 'UNKNOWN')}`\n"
            f"*Trading Status:* `{summary.get('trading_status', 'UNKNOWN')}`\n"
            f"*Circuit Breaker:* `{summary.get('circuit_breaker', 'UNKNOWN')}`\n\n"
            f"*Performance:*\n"
            f"├ Daily PnL: `{summary.get('daily_pnl_percent', 0):+.2f}%`\n"
            f"├ Drawdown: `{summary.get('current_drawdown', 0):.2f}%`\n"
            f"├ CPU: `{summary.get('cpu_percent', 0):.1f}%`\n"
            f"└ Memory: `{summary.get('memory_percent', 0):.1f}%`\n\n"
            f"*Uptime:* `{summary.get('uptime', 'N/A')}`"
        )
        
        await update.message.reply_text(message, parse_mode="Markdown")
    except Exception as e:
        logger.error("Dashboard command error: %s", e)
        await update.message.reply_text(f"❌ Error: {str(e)}")


@require_auth
async def cmd_pnl(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /pnl command."""
    try:
        # Get VGX data
        from app import vgx_snapshot
        vgx = vgx_snapshot()
        
        daily_pnl = vgx.get("daily_pnl", 0)
        total_pnl = vgx.get("total_pnl", 0)
        win_rate = vgx.get("win_rate", 0)
        
        daily_emoji = "📈" if daily_pnl >= 0 else "📉"
        total_emoji = "📈" if total_pnl >= 0 else "📉"
        
        message = (
            f"💰 *Profit & Loss Summary*\n\n"
            f"*VGX Bot:*\n"
            f"{daily_emoji} Daily: `₹{daily_pnl:,.2f}`\n"
            f"{total_emoji} Total: `₹{total_pnl:,.2f}`\n"
            f"📊 Win Rate: `{win_rate:.1f}%`\n"
            f"📝 Trades: `{vgx.get('paper_trades', 0)}`"
        )
        
        await update.message.reply_text(message, parse_mode="Markdown")
    except Exception as e:
        logger.error("PnL command error: %s", e)
        await update.message.reply_text(f"❌ Error: {str(e)}")


@require_auth
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /stats command."""
    try:
        from app import vgx_snapshot
        vgx = vgx_snapshot()
        
        wins = vgx.get("wins", 0)
        losses = vgx.get("losses", 0)
        total = wins + losses
        win_rate = vgx.get("win_rate", 0)
        
        message = (
            f"📊 *Trading Statistics*\n\n"
            f"*Trade Results:*\n"
            f"├ Total Trades: `{total}`\n"
            f"├ Wins: `{wins}` ✅\n"
            f"├ Losses: `{losses}` ❌\n"
            f"└ Win Rate: `{win_rate:.1f}%`\n\n"
            f"*Balance:*\n"
            f"├ Virtual: `₹{vgx.get('virtual_balance', 0):,.2f}`\n"
            f"├ Daily PnL: `₹{vgx.get('daily_pnl', 0):,.2f}`\n"
            f"└ Total PnL: `₹{vgx.get('total_pnl', 0):,.2f}`"
        )
        
        await update.message.reply_text(message, parse_mode="Markdown")
    except Exception as e:
        logger.error("Stats command error: %s", e)
        await update.message.reply_text(f"❌ Error: {str(e)}")


@require_auth
async def cmd_positions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /positions command."""
    try:
        from app import vgx_snapshot
        from bots.mtb_bot.storage import snapshot as mtb_snapshot
        
        vgx = vgx_snapshot()
        mtb = mtb_snapshot()
        
        vgx_positions = vgx.get("open_positions", [])
        mtb_positions = mtb.get("open_positions", [])
        
        total_positions = len(vgx_positions) + len(mtb_positions)
        
        if total_positions == 0:
            await update.message.reply_text("📭 No open positions.")
            return
        
        message = f"📈 *Open Positions ({total_positions})*\n\n"
        
        if vgx_positions:
            message += "*VGX Positions:*\n"
            for pos in vgx_positions[:5]:
                message += (
                    f"├ {pos.get('coin', 'N/A')}: "
                    f"`₹{pos.get('buy_price', 0):,.2f}` x `{pos.get('qty', 0):.4f}`\n"
                )
            if len(vgx_positions) > 5:
                message += f"└ ... and {len(vgx_positions) - 5} more\n"
        
        if mtb_positions:
            message += "\n*MTB Positions:*\n"
            for pos in mtb_positions[:5]:
                message += (
                    f"├ {pos.get('coin', 'N/A')}: "
                    f"`₹{pos.get('entry_price', 0):,.2f}`\n"
                )
        
        await update.message.reply_text(message, parse_mode="Markdown")
    except Exception as e:
        logger.error("Positions command error: %s", e)
        await update.message.reply_text(f"❌ Error: {str(e)}")


@require_auth
async def cmd_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /watchlist command — returns unified scanner watchlist."""
    try:
        from bots.scanner_bot.scanner import get_watchlist
        wl = get_watchlist()
        coins = wl.get("coins", [])
        message = f"👀 *Scanner Watchlist* — `{len(coins)}` coins\n\n"
        if coins:
            coin_list = ", ".join(coins[:20])
            message += f"└ {coin_list}"
            if len(coins) > 20:
                message += f" +{len(coins) - 20} more"
        else:
            message += "└ Empty"
        await update.message.reply_text(message, parse_mode="Markdown")
    except Exception as e:
        logger.error("Watchlist command error: %s", e)
        await update.message.reply_text(f"❌ Error: {str(e)}")


@require_auth
async def cmd_signals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /signals command."""
    try:
        from bots.scanner_bot.scanner import get_signals
        
        signals = get_signals() or []
        
        if not signals:
            await update.message.reply_text("📭 No active signals.")
            return
        
        message = f"📡 *Active Signals ({len(signals)})*\n\n"
        
        tier_emoji = {
            "ELITE": "🏆",
            "HIGH": "⭐",
            "MEDIUM": "📊",
            "LOW": "📉",
        }
        
        for sig in signals[:10]:
            tier = sig.get("tier", "UNKNOWN")
            emoji = tier_emoji.get(tier, "📌")
            message += (
                f"{emoji} *{sig.get('coin', 'N/A')}* ({tier})\n"
                f"└ Score: `{sig.get('score', 0)}` | "
                f"Price: `₹{sig.get('price', 0):,.2f}`\n\n"
            )
        
        if len(signals) > 10:
            message += f"_... and {len(signals) - 10} more signals_"
        
        await update.message.reply_text(message, parse_mode="Markdown")
    except Exception as e:
        logger.error("Signals command error: %s", e)
        await update.message.reply_text(f"❌ Error: {str(e)}")


@require_auth
async def cmd_risk(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /risk command."""
    try:
        from bots.risk_engine.engine import snapshot as risk_snapshot
        
        risk = risk_snapshot()
        
        emergency = risk.get("emergency_stop", False)
        kill_switch = risk.get("kill_switch_active", False)
        
        status_emoji = "🔴" if emergency or kill_switch else "🟢"
        
        message = (
            f"{status_emoji} *Risk Engine Status*\n\n"
            f"*Kill Switches:*\n"
            f"├ Trading Enabled: `{risk.get('trading_enabled', True)}`\n"
            f"├ Kill Switch: `{'ACTIVE' if kill_switch else 'OFF'}`\n"
            f"└ Emergency Stop: `{'ACTIVE' if emergency else 'OFF'}`\n\n"
            f"*Circuit Breaker:*\n"
            f"├ Status: `{risk.get('circuit_breaker_status', 'CLOSED')}`\n"
            f"├ Daily Loss: `{risk.get('daily_loss_pct', 0):.2f}%`\n"
            f"├ Weekly Loss: `{risk.get('weekly_loss_pct', 0):.2f}%`\n"
            f"└ Monthly Loss: `{risk.get('monthly_loss_pct', 0):.2f}%`"
        )
        
        await update.message.reply_text(message, parse_mode="Markdown")
    except Exception as e:
        logger.error("Risk command error: %s", e)
        await update.message.reply_text(f"❌ Error: {str(e)}")


@require_auth
async def cmd_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /portfolio command."""
    try:
        from app import vgx_snapshot, pull_state_payload
        
        vgx = vgx_snapshot()
        state = pull_state_payload()
        
        total_positions = len(vgx.get("open_positions", []))
        virtual_balance = vgx.get("virtual_balance", 0)
        total_pnl = vgx.get("total_pnl", 0)
        
        message = (
            f"💼 *Portfolio Overview*\n\n"
            f"*Balances:*\n"
            f"├ Virtual Balance: `₹{virtual_balance:,.2f}`\n"
            f"├ Total PnL: `₹{total_pnl:,.2f}`\n"
            f"└ Open Positions: `{total_positions}`\n\n"
            f"*Grid Configuration:*\n"
            f"├ Coins: `{', '.join(vgx.get('grid_coins', []))}`\n"
            f"├ Trade Amount: `₹{vgx.get('trade_amount', 0):,.2f}`\n"
            f"├ Target: `{vgx.get('target_pct', 5)}%`\n"
            f"└ Stop Loss: `{vgx.get('stop_loss_pct', 5)}%`"
        )
        
        await update.message.reply_text(message, parse_mode="Markdown")
    except Exception as e:
        logger.error("Portfolio command error: %s", e)
        await update.message.reply_text(f"❌ Error: {str(e)}")


# Admin commands
@require_auth
@require_admin
async def cmd_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /logs command (admin only)."""
    try:
        security = get_security_manager()
        events = security.get_security_events(limit=10)
        
        if not events:
            await update.message.reply_text("📋 No recent security events.")
            return
        
        message = "📋 *Recent Security Events*\n\n"
        
        for event in events[-10:]:
            ts = event.get("timestamp", "")[:19]
            message += (
                f"• `{ts}`\n"
                f"  User: `{event.get('user_id')}` | {event.get('reason', 'N/A')}\n\n"
            )
        
        await update.message.reply_text(message, parse_mode="Markdown")
    except Exception as e:
        logger.error("Logs command error: %s", e)
        await update.message.reply_text(f"❌ Error: {str(e)}")


@require_auth
@require_admin
async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /pause command (admin only)."""
    try:
        os.environ["TRADING_ENABLED"] = "false"
        await update.message.reply_text(
            "⏸️ *Trading Paused*\n\n"
            "New trades are now disabled.\n"
            "Use /resume to re-enable trading.",
            parse_mode="Markdown"
        )
        logger.info("Trading paused by admin: %s", update.effective_user.id)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")


@require_auth
@require_admin
async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /resume command (admin only)."""
    try:
        os.environ["TRADING_ENABLED"] = "true"
        await update.message.reply_text(
            "▶️ *Trading Resumed*\n\n"
            "Trading is now enabled.",
            parse_mode="Markdown"
        )
        logger.info("Trading resumed by admin: %s", update.effective_user.id)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")


@require_auth
@require_admin
async def cmd_emergency(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /emergency command (admin only)."""
    try:
        os.environ["EMERGENCY_STOP"] = "true"
        os.environ["TRADING_ENABLED"] = "false"
        await update.message.reply_text(
            "🚨 *EMERGENCY STOP ACTIVATED*\n\n"
            "All trading has been halted immediately.\n"
            "Contact system administrator.",
            parse_mode="Markdown"
        )
        logger.critical("EMERGENCY STOP activated by admin: %s", update.effective_user.id)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")


@require_auth
@require_admin
async def cmd_restart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /restart command (admin only)."""
    await update.message.reply_text(
        "🔄 *Restart Requested*\n\n"
        "Service restart requires manual intervention in Railway dashboard.",
        parse_mode="Markdown"
    )


# =============================================================================
# PRODUCTION TELEGRAM BOT
# =============================================================================

class ProductionTelegramBot:
    """
    Production-ready Telegram bot with:
    - Automatic startup and reconnection
    - User authentication
    - Trading/Risk/System notifications
    - Full command set
    """
    
    _instance: Optional["ProductionTelegramBot"] = None
    _lock = threading.Lock()
    
    def __new__(cls) -> "ProductionTelegramBot":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._app = None
        self._bot: Optional[Bot] = None
        self._notification_manager: Optional[NotificationManager] = None
        self._running = False
        self._reconnect_task = None
        self._startup_time = datetime.now(timezone.utc)
        
        self._initialized = True
        logger.info("ProductionTelegramBot initialized")
    
    @property
    def notification_manager(self) -> NotificationManager:
        """Get notification manager."""
        if self._notification_manager is None:
            self._notification_manager = NotificationManager(self._bot)
        return self._notification_manager
    
    def _setup_handlers(self) -> None:
        """Setup command handlers."""
        if not self._app:
            return
        
        handlers = [
            ("start", cmd_start),
            ("help", cmd_help),
            ("ping", cmd_ping),
            ("version", cmd_version),
            ("status", cmd_status),
            ("health", cmd_health),
            ("dashboard", cmd_dashboard),
            ("pnl", cmd_pnl),
            ("stats", cmd_stats),
            ("positions", cmd_positions),
            ("watchlist", cmd_watchlist),
            ("signals", cmd_signals),
            ("risk", cmd_risk),
            ("portfolio", cmd_portfolio),
            ("logs", cmd_logs),
            ("pause", cmd_pause),
            ("resume", cmd_resume),
            ("emergency", cmd_emergency),
            ("restart", cmd_restart),
        ]
        
        for cmd, handler in handlers:
            self._app.add_handler(CommandHandler(cmd, handler))
        
        logger.info("Registered %d command handlers", len(handlers))
    
    async def _set_bot_commands(self) -> None:
        """Set bot commands in Telegram."""
        if not self._bot:
            return
        
        commands = [
            BotCommand("start", "Start the bot"),
            BotCommand("help", "Show help"),
            BotCommand("status", "System status"),
            BotCommand("health", "Health check"),
            BotCommand("dashboard", "Dashboard summary"),
            BotCommand("pnl", "Profit/Loss summary"),
            BotCommand("stats", "Trading statistics"),
            BotCommand("positions", "Open positions"),
            BotCommand("signals", "Active signals"),
            BotCommand("watchlist", "Current watchlist"),
            BotCommand("risk", "Risk status"),
            BotCommand("portfolio", "Portfolio overview"),
            BotCommand("ping", "Check bot status"),
            BotCommand("version", "Version info"),
        ]
        
        try:
            await self._bot.set_my_commands(commands)
            logger.info("Bot commands set successfully")
        except Exception as e:
            logger.error("Failed to set bot commands: %s", e)
    
    async def start(self) -> bool:
        """Start the bot."""
        if not TELEGRAM_AVAILABLE:
            logger.error("python-telegram-bot not installed")
            return False
        
        if not TelegramConfig.is_configured():
            logger.warning("Telegram bot not configured (BOT_TOKEN missing)")
            return False
        
        try:
            # Build application
            self._app = (
                ApplicationBuilder()
                .token(TelegramConfig.BOT_TOKEN)
                .build()
            )
            
            self._bot = self._app.bot
            
            # Setup handlers
            self._setup_handlers()
            
            # Initialize notification manager
            self._notification_manager = NotificationManager(self._bot)
            
            # Set commands
            await self._set_bot_commands()
            
            # Start polling
            self._running = True
            
            # Send startup notification
            await self._notification_manager.bot_started()
            
            logger.info("Telegram bot started successfully")
            
            # Start polling in background
            await self._app.initialize()
            await self._app.start()
            await self._app.updater.start_polling(
                drop_pending_updates=True,
                allowed_updates=["message", "callback_query"],
            )
            
            return True
        
        except Exception as e:
            logger.error("Failed to start Telegram bot: %s", e, exc_info=True)
            return False
    
    async def stop(self) -> None:
        """Stop the bot."""
        self._running = False
        
        if self._app:
            try:
                await self._app.updater.stop()
                await self._app.stop()
                await self._app.shutdown()
            except Exception as e:
                logger.error("Error stopping bot: %s", e)
        
        logger.info("Telegram bot stopped")
    
    def run_in_background(self) -> None:
        """Run the bot in a background thread."""
        def _run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            try:
                loop.run_until_complete(self.start())
                loop.run_forever()
            except Exception as e:
                logger.error("Bot thread error: %s", e)
            finally:
                loop.close()
        
        thread = threading.Thread(target=_run, daemon=True, name="TelegramBot")
        thread.start()
        logger.info("Telegram bot started in background thread")
    
    # Notification shortcuts
    async def notify_trade_opened(self, coin: str, qty: float, price: float, source: str = "") -> bool:
        return await self.notification_manager.trade_opened(coin, qty, price, source)
    
    async def notify_trade_closed(self, coin: str, pnl: float, pnl_pct: float, reason: str = "") -> bool:
        return await self.notification_manager.trade_closed(coin, pnl, pnl_pct, reason)
    
    async def notify_circuit_breaker(self, activated: bool, reason: str = "", loss_pct: float = 0) -> bool:
        if activated:
            return await self.notification_manager.circuit_breaker_activated(reason, loss_pct)
        else:
            return await self.notification_manager.circuit_breaker_reset()


# =============================================================================
# SINGLETON ACCESS
# =============================================================================

_bot_instance: Optional[ProductionTelegramBot] = None
_bot_lock = threading.Lock()


def get_telegram_bot() -> ProductionTelegramBot:
    """Get the singleton bot instance."""
    global _bot_instance
    if _bot_instance is None:
        with _bot_lock:
            if _bot_instance is None:
                _bot_instance = ProductionTelegramBot()
    return _bot_instance


def get_notification_manager() -> NotificationManager:
    """Get the notification manager."""
    return get_telegram_bot().notification_manager


# =============================================================================
# STARTUP FUNCTION
# =============================================================================

async def start_telegram_bot() -> bool:
    """Start the Telegram bot (called from app startup)."""
    bot = get_telegram_bot()
    return await bot.start()


def start_telegram_bot_sync() -> None:
    """Start the Telegram bot synchronously in background."""
    bot = get_telegram_bot()
    bot.run_in_background()
