"""
PROJECT-ALPHA Telegram Security Module
User authentication, whitelist management, and access logging.

Security Features:
- User whitelist with admin/user roles
- Admin-only command protection  
- Unauthorized access logging
- Rate limiting per user
"""

import os
import json
import logging
import time
import functools
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Callable, Any, Set
from dataclasses import dataclass, asdict

logger = logging.getLogger("telegram.security")

# ============================================================
# CONFIGURATION
# ============================================================

# Admin users (comma-separated Telegram user IDs)
ADMIN_USER_IDS = set(
    int(uid.strip()) 
    for uid in os.getenv("TELEGRAM_ADMIN_IDS", "").split(",") 
    if uid.strip().isdigit()
)

# Allowed users (comma-separated Telegram user IDs, includes admins)
ALLOWED_USER_IDS = set(
    int(uid.strip()) 
    for uid in os.getenv("TELEGRAM_ALLOWED_IDS", "").split(",") 
    if uid.strip().isdigit()
)

# Include admins in allowed users
ALLOWED_USER_IDS.update(ADMIN_USER_IDS)

# Enable/disable security
SECURITY_ENABLED = os.getenv("TELEGRAM_SECURITY_ENABLED", "true").lower() == "true"

# Security log file
SECURITY_LOG_FILE = Path(os.getenv(
    "SECURITY_LOG_FILE",
    str(Path(__file__).parent / "data" / "security_log.json")
))

# Rate limiting
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))  # seconds
RATE_LIMIT_MAX_REQUESTS = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "30"))  # per window


# ============================================================
# DATA STRUCTURES
# ============================================================

@dataclass
class SecurityEvent:
    """Security event log entry."""
    timestamp: str
    event_type: str         # "access_denied", "admin_action", "rate_limit", "suspicious"
    user_id: int
    username: Optional[str]
    command: str
    details: str
    ip_address: Optional[str] = None


@dataclass
class UserRateLimit:
    """Track rate limiting per user."""
    window_start: float
    request_count: int


# ============================================================
# SECURITY LOG
# ============================================================

class SecurityLogger:
    """Handles security event logging."""
    
    def __init__(self):
        self.events: list = []
        self._load_events()
    
    def _load_events(self) -> None:
        """Load existing security events."""
        if not SECURITY_LOG_FILE.exists():
            return
        try:
            with open(SECURITY_LOG_FILE, "r") as f:
                data = json.load(f)
                self.events = data.get("events", [])[-1000:]  # Keep last 1000
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load security log: %s", e)
    
    def _save_events(self) -> None:
        """Persist security events."""
        SECURITY_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        
        # Keep last 1000 events
        self.events = self.events[-1000:]
        
        tmp_file = SECURITY_LOG_FILE.with_suffix(".tmp")
        with open(tmp_file, "w") as f:
            json.dump({"events": self.events}, f, indent=2)
        tmp_file.replace(SECURITY_LOG_FILE)
    
    def log_event(self, event: SecurityEvent) -> None:
        """Log a security event."""
        event_dict = asdict(event)
        self.events.append(event_dict)
        
        # Also log to standard logger
        if event.event_type == "access_denied":
            logger.warning(
                "SECURITY: Access denied - User %s (%s) attempted: %s",
                event.user_id, event.username, event.command
            )
        elif event.event_type == "rate_limit":
            logger.warning(
                "SECURITY: Rate limit - User %s (%s) exceeded limit: %s",
                event.user_id, event.username, event.details
            )
        elif event.event_type == "suspicious":
            logger.error(
                "SECURITY: Suspicious activity - User %s (%s): %s",
                event.user_id, event.username, event.details
            )
        else:
            logger.info(
                "SECURITY: %s - User %s (%s): %s",
                event.event_type, event.user_id, event.username, event.command
            )
        
        self._save_events()
    
    def get_events(self, user_id: Optional[int] = None, limit: int = 100) -> list:
        """Get security events, optionally filtered by user."""
        events = self.events[-limit:]
        if user_id:
            events = [e for e in events if e.get("user_id") == user_id]
        return events
    
    def get_denied_attempts(self, hours: int = 24) -> list:
        """Get denied access attempts in the last N hours."""
        cutoff = time.time() - (hours * 3600)
        denied = []
        for event in self.events:
            if event.get("event_type") == "access_denied":
                try:
                    ts = datetime.fromisoformat(event.get("timestamp", ""))
                    if ts.timestamp() > cutoff:
                        denied.append(event)
                except ValueError:
                    pass
        return denied


# Global security logger
_security_logger: Optional[SecurityLogger] = None


def get_security_logger() -> SecurityLogger:
    """Get or create security logger instance."""
    global _security_logger
    if _security_logger is None:
        _security_logger = SecurityLogger()
    return _security_logger


# ============================================================
# RATE LIMITING
# ============================================================

_rate_limits: dict = {}


def check_rate_limit(user_id: int) -> tuple:
    """
    Check if user is within rate limit.
    Returns (allowed: bool, remaining: int, reset_in: int)
    """
    now = time.time()
    
    if user_id not in _rate_limits:
        _rate_limits[user_id] = UserRateLimit(window_start=now, request_count=1)
        return True, RATE_LIMIT_MAX_REQUESTS - 1, RATE_LIMIT_WINDOW
    
    limit = _rate_limits[user_id]
    
    # Reset window if expired
    if now - limit.window_start > RATE_LIMIT_WINDOW:
        _rate_limits[user_id] = UserRateLimit(window_start=now, request_count=1)
        return True, RATE_LIMIT_MAX_REQUESTS - 1, RATE_LIMIT_WINDOW
    
    # Check limit
    if limit.request_count >= RATE_LIMIT_MAX_REQUESTS:
        reset_in = int(RATE_LIMIT_WINDOW - (now - limit.window_start))
        return False, 0, reset_in
    
    # Increment counter
    limit.request_count += 1
    remaining = RATE_LIMIT_MAX_REQUESTS - limit.request_count
    reset_in = int(RATE_LIMIT_WINDOW - (now - limit.window_start))
    
    return True, remaining, reset_in


# ============================================================
# AUTHORIZATION FUNCTIONS
# ============================================================

def is_admin(user_id: int) -> bool:
    """Check if user is an admin."""
    if not SECURITY_ENABLED:
        return True
    return user_id in ADMIN_USER_IDS


def is_allowed(user_id: int) -> bool:
    """Check if user is allowed to use the bot."""
    if not SECURITY_ENABLED:
        return True
    if not ALLOWED_USER_IDS:
        # If no whitelist configured, allow all (with warning)
        logger.warning("SECURITY: No whitelist configured - allowing all users")
        return True
    return user_id in ALLOWED_USER_IDS


def add_allowed_user(user_id: int, added_by: int) -> bool:
    """Add a user to the allowed list (admin function)."""
    if not is_admin(added_by):
        return False
    
    ALLOWED_USER_IDS.add(user_id)
    
    get_security_logger().log_event(SecurityEvent(
        timestamp=datetime.now(timezone.utc).isoformat(),
        event_type="admin_action",
        user_id=added_by,
        username=None,
        command=f"add_user {user_id}",
        details=f"Added user {user_id} to whitelist"
    ))
    
    return True


def remove_allowed_user(user_id: int, removed_by: int) -> bool:
    """Remove a user from the allowed list (admin function)."""
    if not is_admin(removed_by):
        return False
    
    # Can't remove admins
    if user_id in ADMIN_USER_IDS:
        return False
    
    ALLOWED_USER_IDS.discard(user_id)
    
    get_security_logger().log_event(SecurityEvent(
        timestamp=datetime.now(timezone.utc).isoformat(),
        event_type="admin_action",
        user_id=removed_by,
        username=None,
        command=f"remove_user {user_id}",
        details=f"Removed user {user_id} from whitelist"
    ))
    
    return True


# ============================================================
# DECORATORS FOR COMMAND PROTECTION
# ============================================================

def require_auth(func: Callable) -> Callable:
    """
    Decorator to require user authentication.
    Use on any Telegram command handler.
    """
    @functools.wraps(func)
    async def wrapper(update, context, *args, **kwargs) -> Any:
        user = update.effective_user
        user_id = user.id if user else 0
        username = user.username if user else "unknown"
        command = update.message.text if update.message else "unknown"
        
        # Check if allowed
        if not is_allowed(user_id):
            get_security_logger().log_event(SecurityEvent(
                timestamp=datetime.now(timezone.utc).isoformat(),
                event_type="access_denied",
                user_id=user_id,
                username=username,
                command=command,
                details="User not in whitelist"
            ))
            
            await update.message.reply_text(
                "⛔ Access Denied\n\n"
                "You are not authorized to use this bot.\n"
                "Contact an administrator for access."
            )
            return None
        
        # Check rate limit
        allowed, remaining, reset_in = check_rate_limit(user_id)
        if not allowed:
            get_security_logger().log_event(SecurityEvent(
                timestamp=datetime.now(timezone.utc).isoformat(),
                event_type="rate_limit",
                user_id=user_id,
                username=username,
                command=command,
                details=f"Rate limit exceeded, reset in {reset_in}s"
            ))
            
            await update.message.reply_text(
                f"⚠️ Rate Limit Exceeded\n\n"
                f"Please wait {reset_in} seconds before trying again."
            )
            return None
        
        return await func(update, context, *args, **kwargs)
    
    return wrapper


def require_admin(func: Callable) -> Callable:
    """
    Decorator to require admin privileges.
    Use on sensitive commands like /setmode, /emergency, /reset.
    """
    @functools.wraps(func)
    async def wrapper(update, context, *args, **kwargs) -> Any:
        user = update.effective_user
        user_id = user.id if user else 0
        username = user.username if user else "unknown"
        command = update.message.text if update.message else "unknown"
        
        # Check basic auth first
        if not is_allowed(user_id):
            get_security_logger().log_event(SecurityEvent(
                timestamp=datetime.now(timezone.utc).isoformat(),
                event_type="access_denied",
                user_id=user_id,
                username=username,
                command=command,
                details="User not in whitelist"
            ))
            
            await update.message.reply_text("⛔ Access Denied")
            return None
        
        # Check admin
        if not is_admin(user_id):
            get_security_logger().log_event(SecurityEvent(
                timestamp=datetime.now(timezone.utc).isoformat(),
                event_type="access_denied",
                user_id=user_id,
                username=username,
                command=command,
                details="Admin privileges required"
            ))
            
            await update.message.reply_text(
                "⛔ Admin Access Required\n\n"
                "This command requires administrator privileges."
            )
            return None
        
        # Log admin action
        get_security_logger().log_event(SecurityEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            event_type="admin_action",
            user_id=user_id,
            username=username,
            command=command,
            details="Admin command executed"
        ))
        
        # Check rate limit (stricter for admins to prevent abuse)
        allowed, _, reset_in = check_rate_limit(user_id)
        if not allowed:
            await update.message.reply_text(
                f"⚠️ Rate Limit - Please wait {reset_in}s"
            )
            return None
        
        return await func(update, context, *args, **kwargs)
    
    return wrapper


# ============================================================
# ADMIN COMMANDS
# ============================================================

async def security_status_cmd(update, context) -> None:
    """Show security status (admin only)."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin access required")
        return
    
    sec_logger = get_security_logger()
    denied_24h = sec_logger.get_denied_attempts(24)
    
    status = (
        "🔒 **Security Status**\n\n"
        f"Security Enabled: {SECURITY_ENABLED}\n"
        f"Admin Users: {len(ADMIN_USER_IDS)}\n"
        f"Allowed Users: {len(ALLOWED_USER_IDS)}\n"
        f"Denied Attempts (24h): {len(denied_24h)}\n"
        f"Rate Limit: {RATE_LIMIT_MAX_REQUESTS}/{RATE_LIMIT_WINDOW}s\n\n"
    )
    
    if denied_24h:
        status += "**Recent Denials:**\n"
        for event in denied_24h[-5:]:
            status += f"- User {event['user_id']}: {event['command']}\n"
    
    await update.message.reply_text(status, parse_mode="Markdown")


async def add_user_cmd(update, context) -> None:
    """Add user to whitelist (admin only)."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin access required")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /adduser <user_id>")
        return
    
    try:
        new_user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid user ID")
        return
    
    if add_allowed_user(new_user_id, update.effective_user.id):
        await update.message.reply_text(f"✅ User {new_user_id} added to whitelist")
    else:
        await update.message.reply_text("❌ Failed to add user")


async def remove_user_cmd(update, context) -> None:
    """Remove user from whitelist (admin only)."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin access required")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /removeuser <user_id>")
        return
    
    try:
        target_user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid user ID")
        return
    
    if remove_allowed_user(target_user_id, update.effective_user.id):
        await update.message.reply_text(f"✅ User {target_user_id} removed from whitelist")
    else:
        await update.message.reply_text("❌ Failed to remove user (may be admin)")


async def security_log_cmd(update, context) -> None:
    """View security log (admin only)."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin access required")
        return
    
    sec_logger = get_security_logger()
    events = sec_logger.get_events(limit=10)
    
    if not events:
        await update.message.reply_text("📋 No security events logged")
        return
    
    log_text = "📋 **Recent Security Events**\n\n"
    for event in reversed(events):
        event_type = event.get("event_type", "unknown")
        user_id = event.get("user_id", 0)
        cmd = event.get("command", "")[:30]
        log_text += f"• [{event_type}] User {user_id}: {cmd}\n"
    
    await update.message.reply_text(log_text, parse_mode="Markdown")


# ============================================================
# UTILITY
# ============================================================

def get_security_status() -> dict:
    """Get security status for API/dashboard."""
    sec_logger = get_security_logger()
    denied_24h = sec_logger.get_denied_attempts(24)
    
    return {
        "enabled": SECURITY_ENABLED,
        "admin_count": len(ADMIN_USER_IDS),
        "allowed_count": len(ALLOWED_USER_IDS),
        "denied_24h": len(denied_24h),
        "rate_limit": {
            "window": RATE_LIMIT_WINDOW,
            "max_requests": RATE_LIMIT_MAX_REQUESTS
        },
        "active_rate_limits": len(_rate_limits)
    }
