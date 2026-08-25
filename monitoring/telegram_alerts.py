"""
Telegram Alert System - Production Alert Notifications
=======================================================

Sends automated alerts to Telegram for critical system events:
- Circuit breaker activation
- Loss limit breaches (daily/weekly/monthly)
- Storage corruption detection
- High resource usage (CPU/Memory)
- API failures
- Security events

Thread-safe with rate limiting and message queuing.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("monitoring.alerts")


# =============================================================================
# ENUMS AND CONSTANTS
# =============================================================================

class AlertSeverity(Enum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"
    INFO = "INFO"
    RESOLVED = "RESOLVED"


class AlertCategory(Enum):
    CIRCUIT_BREAKER = "circuit_breaker"
    LOSS_LIMIT = "loss_limit"
    STORAGE = "storage"
    SYSTEM = "system"
    SECURITY = "security"
    API = "api"
    TRADING = "trading"


# Emoji mappings
SEVERITY_EMOJI = {
    AlertSeverity.CRITICAL: "🚨",
    AlertSeverity.WARNING: "⚠️",
    AlertSeverity.INFO: "ℹ️",
    AlertSeverity.RESOLVED: "✅",
}

CATEGORY_EMOJI = {
    AlertCategory.CIRCUIT_BREAKER: "🔴",
    AlertCategory.LOSS_LIMIT: "📉",
    AlertCategory.STORAGE: "💾",
    AlertCategory.SYSTEM: "🖥️",
    AlertCategory.SECURITY: "🔒",
    AlertCategory.API: "🌐",
    AlertCategory.TRADING: "📊",
}


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class Alert:
    """Single alert instance."""
    category: AlertCategory
    severity: AlertSeverity
    title: str
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    alert_id: str = ""
    
    def __post_init__(self):
        if not self.alert_id:
            self.alert_id = f"{self.category.value}_{int(self.timestamp.timestamp())}"
    
    def format_telegram(self) -> str:
        """Format alert for Telegram message."""
        sev_emoji = SEVERITY_EMOJI.get(self.severity, "")
        cat_emoji = CATEGORY_EMOJI.get(self.category, "")
        
        lines = [
            f"{sev_emoji} *{self.severity.value}* {cat_emoji}",
            f"*{self.title}*",
            "",
            self.message,
        ]
        
        if self.details:
            lines.append("")
            lines.append("*Details:*")
            for key, value in self.details.items():
                lines.append(f"• {key}: `{value}`")
        
        lines.append("")
        lines.append(f"_Time: {self.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}_")
        
        return "\n".join(lines)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "category": self.category.value,
            "severity": self.severity.value,
            "title": self.title,
            "message": self.message,
            "details": self.details,
            "timestamp": self.timestamp.isoformat(),
        }


# =============================================================================
# TELEGRAM SENDER
# =============================================================================

class TelegramAlertSender:
    """
    Sends alerts to Telegram with rate limiting.
    
    Features:
    - Rate limiting (max 20 messages/minute)
    - Message queuing
    - Retry logic
    - De-duplication (same alert within cooldown)
    """
    
    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        rate_limit: int = 20,
        rate_window: int = 60,
        cooldown_seconds: int = 300,
    ):
        # Multi-bot configuration - V1
        # ENV: ALERT_BOT_TOKEN - Telegram bot token for system alerts (fallback: BOT_TOKEN)
        # ENV: ALERT_CHAT_ID - Chat ID for system alerts (fallback: TELEGRAM_CHAT_ID)
        self._bot_token = bot_token or os.getenv("ALERT_BOT_TOKEN") or os.getenv("BOT_TOKEN")
        self._chat_id = chat_id or os.getenv("ALERT_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
        self._rate_limit = rate_limit
        self._rate_window = rate_window
        self._cooldown_seconds = cooldown_seconds
        
        self._lock = threading.Lock()
        self._message_times: deque = deque(maxlen=rate_limit)
        self._sent_alerts: Dict[str, datetime] = {}  # alert_id -> last_sent
        self._queue: deque = deque(maxlen=100)
        self._running = False
        self._worker_thread: Optional[threading.Thread] = None
        
        logger.info("TelegramAlertSender initialized (token=%s, chat=%s)", 
                    "***" if self._bot_token else "MISSING",
                    self._chat_id or "MISSING")
    
    def is_configured(self) -> bool:
        """Check if Telegram is properly configured."""
        return bool(self._bot_token and self._chat_id)
    
    def _can_send(self) -> bool:
        """Check rate limit."""
        now = time.time()
        with self._lock:
            # Clean old timestamps
            while self._message_times and now - self._message_times[0] > self._rate_window:
                self._message_times.popleft()
            return len(self._message_times) < self._rate_limit
    
    def _is_duplicate(self, alert: Alert) -> bool:
        """Check if alert was recently sent."""
        with self._lock:
            last_sent = self._sent_alerts.get(alert.alert_id)
            if last_sent:
                age = (datetime.now(timezone.utc) - last_sent).total_seconds()
                if age < self._cooldown_seconds:
                    return True
            return False
    
    def _mark_sent(self, alert: Alert) -> None:
        """Mark alert as sent."""
        with self._lock:
            self._sent_alerts[alert.alert_id] = datetime.now(timezone.utc)
            self._message_times.append(time.time())
            # Cleanup old entries
            cutoff = datetime.now(timezone.utc) - timedelta(seconds=self._cooldown_seconds * 2)
            self._sent_alerts = {
                k: v for k, v in self._sent_alerts.items()
                if v > cutoff
            }
    
    def send_alert(self, alert: Alert) -> bool:
        """Send alert to Telegram (synchronous)."""
        if not self.is_configured():
            logger.warning("Telegram not configured, alert not sent: %s", alert.title)
            return False
        
        if self._is_duplicate(alert):
            logger.debug("Duplicate alert suppressed: %s", alert.alert_id)
            return False
        
        if not self._can_send():
            logger.warning("Rate limit reached, queueing alert: %s", alert.title)
            with self._lock:
                self._queue.append(alert)
            return False
        
        try:
            import requests
            url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
            payload = {
                "chat_id": self._chat_id,
                "text": alert.format_telegram(),
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            }
            
            response = requests.post(url, json=payload, timeout=10)
            
            if response.status_code == 200:
                self._mark_sent(alert)
                logger.info("Alert sent: %s", alert.title)
                return True
            else:
                logger.error("Telegram API error %d: %s", response.status_code, response.text)
                return False
        
        except Exception as e:
            logger.error("Failed to send alert: %s", e, exc_info=True)
            return False
    
    async def send_alert_async(self, alert: Alert) -> bool:
        """Send alert asynchronously."""
        return await asyncio.to_thread(self.send_alert, alert)
    
    def start_worker(self) -> None:
        """Start background worker for queued alerts."""
        if self._running:
            return
        self._running = True
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()
        logger.info("Alert worker started")
    
    def stop_worker(self) -> None:
        """Stop background worker."""
        self._running = False
        if self._worker_thread:
            self._worker_thread.join(timeout=5)
        logger.info("Alert worker stopped")
    
    def _worker_loop(self) -> None:
        """Background worker to process queued alerts."""
        while self._running:
            try:
                if self._queue and self._can_send():
                    with self._lock:
                        if self._queue:
                            alert = self._queue.popleft()
                    self.send_alert(alert)
                time.sleep(1)
            except Exception as e:
                logger.error("Worker error: %s", e)
                time.sleep(5)


# =============================================================================
# ALERT MANAGER - SINGLETON
# =============================================================================

class AlertManager:
    """
    Central alert management with automatic triggers.
    
    Monitors metrics and triggers alerts based on thresholds.
    Thread-safe singleton implementation.
    """
    
    _instance: Optional["AlertManager"] = None
    _lock = threading.Lock()
    
    def __new__(cls) -> "AlertManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._telegram = TelegramAlertSender()
        self._alert_history: deque = deque(maxlen=500)
        self._alert_lock = threading.Lock()
        self._callbacks: List[Callable[[Alert], None]] = []
        
        # Alert thresholds
        self._thresholds = {
            "cpu_warning": 85.0,
            "cpu_critical": 95.0,
            "memory_warning": 85.0,
            "memory_critical": 95.0,
            "daily_loss_limit": 3.0,  # percent
            "weekly_loss_limit": 8.0,
            "monthly_loss_limit": 12.0,
            "failed_logins_threshold": 5,
            "rate_limit_threshold": 20,
        }
        
        # Tracking for state-based alerts (to detect changes)
        self._last_states: Dict[str, Any] = {}
        
        self._initialized = True
        logger.info("AlertManager initialized")
    
    def set_threshold(self, key: str, value: float) -> None:
        """Set alert threshold."""
        self._thresholds[key] = value
    
    def register_callback(self, callback: Callable[[Alert], None]) -> None:
        """Register callback for all alerts."""
        with self._alert_lock:
            self._callbacks.append(callback)
    
    def _trigger_callbacks(self, alert: Alert) -> None:
        """Trigger registered callbacks."""
        for callback in self._callbacks:
            try:
                callback(alert)
            except Exception as e:
                logger.error("Alert callback error: %s", e)
    
    def send_alert(
        self,
        category: AlertCategory,
        severity: AlertSeverity,
        title: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> Alert:
        """Send an alert."""
        alert = Alert(
            category=category,
            severity=severity,
            title=title,
            message=message,
            details=details or {},
        )
        
        with self._alert_lock:
            self._alert_history.append(alert)
        
        # Send via Telegram
        self._telegram.send_alert(alert)
        
        # Trigger callbacks
        self._trigger_callbacks(alert)
        
        return alert
    
    def get_alert_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent alert history."""
        with self._alert_lock:
            return [a.to_dict() for a in list(self._alert_history)[-limit:]]
    
    # -------------------------------------------------------------------------
    # SPECIFIC ALERT TRIGGERS
    # -------------------------------------------------------------------------
    
    def alert_circuit_breaker_activated(self, reason: str, details: Optional[Dict] = None) -> Alert:
        """Circuit breaker activation alert."""
        return self.send_alert(
            category=AlertCategory.CIRCUIT_BREAKER,
            severity=AlertSeverity.CRITICAL,
            title="Circuit Breaker ACTIVATED",
            message=f"Trading has been halted.\n\nReason: {reason}",
            details=details,
        )
    
    def alert_circuit_breaker_reset(self) -> Alert:
        """Circuit breaker reset alert."""
        return self.send_alert(
            category=AlertCategory.CIRCUIT_BREAKER,
            severity=AlertSeverity.RESOLVED,
            title="Circuit Breaker Reset",
            message="Trading has resumed. Circuit breaker is now closed.",
        )
    
    def alert_daily_loss_limit(self, loss_percent: float, limit: float) -> Alert:
        """Daily loss limit reached."""
        return self.send_alert(
            category=AlertCategory.LOSS_LIMIT,
            severity=AlertSeverity.CRITICAL,
            title="Daily Loss Limit Reached",
            message=f"Daily loss of {loss_percent:.2f}% has exceeded the {limit:.2f}% limit.",
            details={"loss_percent": loss_percent, "limit": limit},
        )
    
    def alert_weekly_loss_limit(self, loss_percent: float, limit: float) -> Alert:
        """Weekly loss limit reached."""
        return self.send_alert(
            category=AlertCategory.LOSS_LIMIT,
            severity=AlertSeverity.CRITICAL,
            title="Weekly Loss Limit Reached",
            message=f"Weekly loss of {loss_percent:.2f}% has exceeded the {limit:.2f}% limit.",
            details={"loss_percent": loss_percent, "limit": limit},
        )
    
    def alert_monthly_loss_limit(self, loss_percent: float, limit: float) -> Alert:
        """Monthly loss limit reached."""
        return self.send_alert(
            category=AlertCategory.LOSS_LIMIT,
            severity=AlertSeverity.CRITICAL,
            title="Monthly Loss Limit Reached",
            message=f"Monthly loss of {loss_percent:.2f}% has exceeded the {limit:.2f}% limit.",
            details={"loss_percent": loss_percent, "limit": limit},
        )
    
    def alert_storage_corruption(self, filename: str, error: str) -> Alert:
        """Storage file corruption detected."""
        return self.send_alert(
            category=AlertCategory.STORAGE,
            severity=AlertSeverity.CRITICAL,
            title="Storage Corruption Detected",
            message=f"File `{filename}` appears to be corrupted.\n\n{error}",
            details={"filename": filename, "error": error},
        )
    
    def alert_backup_restored(self, filename: str) -> Alert:
        """Backup restoration alert."""
        return self.send_alert(
            category=AlertCategory.STORAGE,
            severity=AlertSeverity.WARNING,
            title="Backup Restored",
            message=f"File `{filename}` was restored from backup.",
            details={"filename": filename},
        )
    
    def alert_high_cpu(self, cpu_percent: float) -> Alert:
        """High CPU usage alert."""
        severity = AlertSeverity.CRITICAL if cpu_percent >= self._thresholds["cpu_critical"] else AlertSeverity.WARNING
        return self.send_alert(
            category=AlertCategory.SYSTEM,
            severity=severity,
            title="High CPU Usage",
            message=f"CPU usage is at {cpu_percent:.1f}%",
            details={"cpu_percent": cpu_percent},
        )
    
    def alert_high_memory(self, memory_percent: float) -> Alert:
        """High memory usage alert."""
        severity = AlertSeverity.CRITICAL if memory_percent >= self._thresholds["memory_critical"] else AlertSeverity.WARNING
        return self.send_alert(
            category=AlertCategory.SYSTEM,
            severity=severity,
            title="High Memory Usage",
            message=f"Memory usage is at {memory_percent:.1f}%",
            details={"memory_percent": memory_percent},
        )
    
    def alert_api_failure(self, endpoint: str, error: str) -> Alert:
        """API failure alert."""
        return self.send_alert(
            category=AlertCategory.API,
            severity=AlertSeverity.WARNING,
            title="API Failure",
            message=f"Endpoint `{endpoint}` is failing.\n\n{error}",
            details={"endpoint": endpoint, "error": error},
        )
    
    def alert_unauthorized_access(self, user_id: Optional[str], ip_address: Optional[str], details: Optional[str] = None) -> Alert:
        """Unauthorized access attempt alert."""
        return self.send_alert(
            category=AlertCategory.SECURITY,
            severity=AlertSeverity.WARNING,
            title="Unauthorized Access Attempt",
            message=f"Unauthorized access attempt detected.",
            details={
                "user_id": user_id or "Unknown",
                "ip_address": ip_address or "Unknown",
                "details": details or "",
            },
        )
    
    def alert_multiple_failed_logins(self, count: int, ip_address: Optional[str] = None) -> Alert:
        """Multiple failed login attempts alert."""
        return self.send_alert(
            category=AlertCategory.SECURITY,
            severity=AlertSeverity.WARNING,
            title="Multiple Failed Login Attempts",
            message=f"{count} failed login attempts detected in the last hour.",
            details={"count": count, "ip_address": ip_address or "Multiple"},
        )
    
    # -------------------------------------------------------------------------
    # METRIC MONITORING
    # -------------------------------------------------------------------------
    
    def check_metrics_and_alert(self, metrics: Dict[str, Any]) -> List[Alert]:
        """
        Check metrics against thresholds and trigger alerts.
        
        Call this periodically with collected metrics.
        Returns list of alerts triggered.
        """
        alerts = []
        
        # CPU check
        cpu = metrics.get("system", {}).get("cpu_percent", 0)
        if cpu >= self._thresholds["cpu_warning"]:
            # Only alert if state changed
            if self._last_states.get("cpu_high") != True:
                alerts.append(self.alert_high_cpu(cpu))
                self._last_states["cpu_high"] = True
        else:
            self._last_states["cpu_high"] = False
        
        # Memory check
        memory = metrics.get("system", {}).get("memory_percent", 0)
        if memory >= self._thresholds["memory_warning"]:
            if self._last_states.get("memory_high") != True:
                alerts.append(self.alert_high_memory(memory))
                self._last_states["memory_high"] = True
        else:
            self._last_states["memory_high"] = False
        
        # PnL checks
        safety = metrics.get("safety", {})
        pnl = safety.get("pnl", {})
        
        daily_pnl_pct = abs(pnl.get("daily_pnl_percent", 0))
        if daily_pnl_pct >= self._thresholds["daily_loss_limit"] and pnl.get("daily_pnl", 0) < 0:
            if self._last_states.get("daily_loss") != True:
                alerts.append(self.alert_daily_loss_limit(daily_pnl_pct, self._thresholds["daily_loss_limit"]))
                self._last_states["daily_loss"] = True
        else:
            self._last_states["daily_loss"] = False
        
        weekly_pnl_pct = abs(pnl.get("weekly_pnl_percent", 0))
        if weekly_pnl_pct >= self._thresholds["weekly_loss_limit"] and pnl.get("weekly_pnl", 0) < 0:
            if self._last_states.get("weekly_loss") != True:
                alerts.append(self.alert_weekly_loss_limit(weekly_pnl_pct, self._thresholds["weekly_loss_limit"]))
                self._last_states["weekly_loss"] = True
        else:
            self._last_states["weekly_loss"] = False
        
        monthly_pnl_pct = abs(pnl.get("monthly_pnl_percent", 0))
        if monthly_pnl_pct >= self._thresholds["monthly_loss_limit"] and pnl.get("monthly_pnl", 0) < 0:
            if self._last_states.get("monthly_loss") != True:
                alerts.append(self.alert_monthly_loss_limit(monthly_pnl_pct, self._thresholds["monthly_loss_limit"]))
                self._last_states["monthly_loss"] = True
        else:
            self._last_states["monthly_loss"] = False
        
        # Circuit breaker check
        cb_status = safety.get("circuit_breaker_status", "CLOSED")
        if cb_status == "OPEN" and self._last_states.get("circuit_breaker") != "OPEN":
            alerts.append(self.alert_circuit_breaker_activated("Triggered by monitoring", {"status": cb_status}))
        elif cb_status == "CLOSED" and self._last_states.get("circuit_breaker") == "OPEN":
            alerts.append(self.alert_circuit_breaker_reset())
        self._last_states["circuit_breaker"] = cb_status
        
        # Storage checks
        storage = metrics.get("storage", {})
        for filename, file_metrics in storage.items():
            if isinstance(file_metrics, dict):
                status = file_metrics.get("status", "HEALTHY")
                if status == "CORRUPTED":
                    state_key = f"storage_{filename}"
                    if self._last_states.get(state_key) != "CORRUPTED":
                        alerts.append(self.alert_storage_corruption(
                            filename, file_metrics.get("error_message", "Unknown error")
                        ))
                        self._last_states[state_key] = "CORRUPTED"
                else:
                    self._last_states[f"storage_{filename}"] = status
        
        # Security checks
        security = metrics.get("security", {})
        failed_logins = security.get("failed_login_last_hour", 0)
        if failed_logins >= self._thresholds["failed_logins_threshold"]:
            if self._last_states.get("failed_logins") != True:
                alerts.append(self.alert_multiple_failed_logins(failed_logins))
                self._last_states["failed_logins"] = True
        else:
            self._last_states["failed_logins"] = False
        
        return alerts
    
    def start_monitoring(self, interval_seconds: int = 60) -> None:
        """Start background monitoring thread."""
        self._telegram.start_worker()
        
        def _monitor_loop():
            while True:
                try:
                    from .metrics_collector import get_metrics_collector
                    collector = get_metrics_collector()
                    metrics = collector.collect_all()
                    self.check_metrics_and_alert(metrics)
                except Exception as e:
                    logger.error("Monitoring error: %s", e)
                time.sleep(interval_seconds)
        
        thread = threading.Thread(target=_monitor_loop, daemon=True)
        thread.start()
        logger.info("Alert monitoring started (interval=%ds)", interval_seconds)
    
    def stop_monitoring(self) -> None:
        """Stop monitoring."""
        self._telegram.stop_worker()


# =============================================================================
# SINGLETON ACCESSOR
# =============================================================================

_alert_manager: Optional[AlertManager] = None
_alert_lock = threading.Lock()


def get_alert_manager() -> AlertManager:
    """Get the singleton AlertManager instance."""
    global _alert_manager
    if _alert_manager is None:
        with _alert_lock:
            if _alert_manager is None:
                _alert_manager = AlertManager()
    return _alert_manager


# =============================================================================
# HEALTH CHECK CALLBACK INTEGRATION
# =============================================================================

def register_alert_callbacks() -> None:
    """Register alert manager with health checker for automatic alerting."""
    try:
        from .health_check import get_health_checker, HealthCheckResult, HealthStatus
        
        checker = get_health_checker()
        manager = get_alert_manager()
        
        def _health_alert_callback(result: HealthCheckResult) -> None:
            """Convert unhealthy health checks to alerts."""
            if result.status == HealthStatus.UNHEALTHY:
                manager.send_alert(
                    category=AlertCategory.SYSTEM,
                    severity=AlertSeverity.CRITICAL,
                    title=f"Health Check Failed: {result.name}",
                    message=result.message,
                    details=result.details,
                )
            elif result.status == HealthStatus.DEGRADED:
                manager.send_alert(
                    category=AlertCategory.SYSTEM,
                    severity=AlertSeverity.WARNING,
                    title=f"Health Check Degraded: {result.name}",
                    message=result.message,
                    details=result.details,
                )
        
        checker.register_alert_callback(_health_alert_callback)
        logger.info("Alert callbacks registered with health checker")
    
    except Exception as e:
        logger.error("Failed to register alert callbacks: %s", e)
