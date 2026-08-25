"""
Metrics Collector - Thread-Safe Metrics Aggregation System
===========================================================

Collects and aggregates metrics from all PROJECT ALPHA subsystems:
- Trading metrics (PnL, win rate, drawdown)
- System metrics (CPU, memory, threads)
- Security metrics (auth attempts, rate limits)
- Storage metrics (file status, checksums)

Thread-safe singleton implementation with atomic operations.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable
from enum import Enum

logger = logging.getLogger("monitoring.metrics")


# =============================================================================
# ENUMS AND CONSTANTS
# =============================================================================

class TradingStatus(Enum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    EMERGENCY = "EMERGENCY"
    UNKNOWN = "UNKNOWN"


class CircuitBreakerStatus(Enum):
    CLOSED = "CLOSED"      # Normal operation
    OPEN = "OPEN"          # Tripped - trading halted
    HALF_OPEN = "HALF_OPEN"  # Testing recovery


class FileStatus(Enum):
    HEALTHY = "HEALTHY"
    MISSING = "MISSING"
    CORRUPTED = "CORRUPTED"
    LOCKED = "LOCKED"
    STALE = "STALE"


# Default paths - can be overridden via environment
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_STORAGE_DIR = Path(__file__).resolve().parent.parent / "storage"


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class PnLMetrics:
    """Profit and Loss metrics across time periods."""
    daily_pnl: float = 0.0
    daily_pnl_percent: float = 0.0
    weekly_pnl: float = 0.0
    weekly_pnl_percent: float = 0.0
    monthly_pnl: float = 0.0
    monthly_pnl_percent: float = 0.0
    total_pnl: float = 0.0
    total_pnl_percent: float = 0.0
    last_updated: Optional[datetime] = None


@dataclass
class DrawdownMetrics:
    """Drawdown tracking metrics."""
    current_drawdown_percent: float = 0.0
    max_drawdown_percent: float = 0.0
    peak_equity: float = 0.0
    current_equity: float = 0.0
    drawdown_start: Optional[datetime] = None
    days_in_drawdown: int = 0


@dataclass
class TradingStats:
    """Comprehensive trading statistics."""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    loss_rate: float = 0.0
    profit_factor: float = 0.0
    average_win: float = 0.0
    average_loss: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    max_drawdown: float = 0.0
    open_positions: int = 0
    total_volume: float = 0.0
    last_trade_time: Optional[datetime] = None


@dataclass
class FileHealthMetrics:
    """Health metrics for a single storage file."""
    filename: str
    path: str
    status: FileStatus = FileStatus.MISSING
    size_bytes: int = 0
    checksum: str = ""
    last_modified: Optional[datetime] = None
    last_checked: Optional[datetime] = None
    record_count: int = 0
    is_valid_json: bool = False
    backup_available: bool = False
    backup_age_hours: float = 0.0
    error_message: str = ""


@dataclass
class SecurityMetrics:
    """Security event metrics."""
    authorized_users: List[str] = field(default_factory=list)
    failed_login_attempts: int = 0
    failed_login_last_hour: int = 0
    rate_limit_violations: int = 0
    rate_limit_violations_last_hour: int = 0
    blocked_ips: List[str] = field(default_factory=list)
    last_security_event: Optional[datetime] = None
    security_events_today: int = 0


@dataclass
class SystemMetrics:
    """System resource utilization metrics."""
    cpu_percent: float = 0.0
    cpu_count: int = 0
    memory_percent: float = 0.0
    memory_used_mb: float = 0.0
    memory_total_mb: float = 0.0
    disk_percent: float = 0.0
    disk_used_gb: float = 0.0
    disk_total_gb: float = 0.0
    uptime_seconds: float = 0.0
    uptime_formatted: str = ""
    thread_count: int = 0
    open_files: int = 0
    network_connections: int = 0
    process_id: int = 0
    api_latency_ms: float = 0.0
    last_measured: Optional[datetime] = None


@dataclass
class SafetyDashboardData:
    """Aggregated safety dashboard data."""
    trading_status: TradingStatus = TradingStatus.UNKNOWN
    circuit_breaker_status: CircuitBreakerStatus = CircuitBreakerStatus.CLOSED
    pnl: PnLMetrics = field(default_factory=PnLMetrics)
    drawdown: DrawdownMetrics = field(default_factory=DrawdownMetrics)
    kill_switch_active: bool = False
    emergency_stop_active: bool = False
    last_updated: Optional[datetime] = None


# =============================================================================
# METRICS COLLECTOR - SINGLETON
# =============================================================================

class MetricsCollector:
    """
    Thread-safe metrics collector singleton.
    
    Aggregates metrics from all subsystems with atomic read/write operations.
    Supports custom metric callbacks for extensibility.
    """
    
    _instance: Optional["MetricsCollector"] = None
    _lock = threading.Lock()
    
    def __new__(cls) -> "MetricsCollector":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._data_lock = threading.RLock()
        self._callback_lock = threading.Lock()
        
        # Metric storage
        self._safety_data = SafetyDashboardData()
        self._trading_stats = TradingStats()
        self._system_metrics = SystemMetrics()
        self._security_metrics = SecurityMetrics()
        self._file_health: Dict[str, FileHealthMetrics] = {}
        
        # Event logs (ring buffer style - keep last N)
        self._security_events: List[Dict[str, Any]] = []
        self._trade_events: List[Dict[str, Any]] = []
        self._MAX_EVENTS = 1000
        
        # Custom callbacks for metric sources
        self._metric_callbacks: Dict[str, Callable] = {}
        
        # Timing
        self._start_time = time.time()
        self._last_collect_time: Optional[float] = None
        
        # Configuration
        self._data_dir = Path(os.getenv("DATA_DIR", str(DEFAULT_DATA_DIR)))
        self._storage_dir = Path(os.getenv("STORAGE_DIR", str(DEFAULT_STORAGE_DIR)))
        
        # Monitored files
        self._monitored_files = [
            "positions.json",
            "trade_history.json", 
            "analytics.json",
            "stats.json",
            "signals.json",
        ]
        
        self._initialized = True
        logger.info("MetricsCollector initialized")
    
    # -------------------------------------------------------------------------
    # SAFETY METRICS
    # -------------------------------------------------------------------------
    
    def update_trading_status(self, status: TradingStatus) -> None:
        """Update the current trading status."""
        with self._data_lock:
            self._safety_data.trading_status = status
            self._safety_data.last_updated = datetime.now(timezone.utc)
    
    def update_circuit_breaker(self, status: CircuitBreakerStatus) -> None:
        """Update circuit breaker status."""
        with self._data_lock:
            self._safety_data.circuit_breaker_status = status
            self._safety_data.last_updated = datetime.now(timezone.utc)
    
    def update_pnl(
        self,
        daily: float = 0.0,
        weekly: float = 0.0,
        monthly: float = 0.0,
        total: float = 0.0,
        starting_capital: float = 100000.0
    ) -> None:
        """Update PnL metrics with percentage calculations."""
        with self._data_lock:
            pnl = self._safety_data.pnl
            pnl.daily_pnl = daily
            pnl.weekly_pnl = weekly
            pnl.monthly_pnl = monthly
            pnl.total_pnl = total
            
            if starting_capital > 0:
                pnl.daily_pnl_percent = (daily / starting_capital) * 100
                pnl.weekly_pnl_percent = (weekly / starting_capital) * 100
                pnl.monthly_pnl_percent = (monthly / starting_capital) * 100
                pnl.total_pnl_percent = (total / starting_capital) * 100
            
            pnl.last_updated = datetime.now(timezone.utc)
    
    def update_drawdown(
        self,
        current_equity: float,
        peak_equity: float,
        drawdown_start: Optional[datetime] = None
    ) -> None:
        """Update drawdown metrics."""
        with self._data_lock:
            dd = self._safety_data.drawdown
            dd.current_equity = current_equity
            dd.peak_equity = peak_equity
            
            if peak_equity > 0:
                dd.current_drawdown_percent = ((peak_equity - current_equity) / peak_equity) * 100
                dd.max_drawdown_percent = max(dd.max_drawdown_percent, dd.current_drawdown_percent)
            
            if drawdown_start:
                dd.drawdown_start = drawdown_start
                dd.days_in_drawdown = (datetime.now(timezone.utc) - drawdown_start).days
    
    def set_kill_switch(self, active: bool) -> None:
        """Set kill switch status."""
        with self._data_lock:
            self._safety_data.kill_switch_active = active
            self._safety_data.last_updated = datetime.now(timezone.utc)
    
    def set_emergency_stop(self, active: bool) -> None:
        """Set emergency stop status."""
        with self._data_lock:
            self._safety_data.emergency_stop_active = active
            if active:
                self._safety_data.trading_status = TradingStatus.EMERGENCY
            self._safety_data.last_updated = datetime.now(timezone.utc)
    
    def get_safety_dashboard(self) -> SafetyDashboardData:
        """Get current safety dashboard data."""
        with self._data_lock:
            return SafetyDashboardData(
                trading_status=self._safety_data.trading_status,
                circuit_breaker_status=self._safety_data.circuit_breaker_status,
                pnl=PnLMetrics(**self._safety_data.pnl.__dict__),
                drawdown=DrawdownMetrics(**self._safety_data.drawdown.__dict__),
                kill_switch_active=self._safety_data.kill_switch_active,
                emergency_stop_active=self._safety_data.emergency_stop_active,
                last_updated=self._safety_data.last_updated,
            )
    
    # -------------------------------------------------------------------------
    # TRADING STATISTICS
    # -------------------------------------------------------------------------
    
    def update_trading_stats(
        self,
        total_trades: int = 0,
        winning_trades: int = 0,
        losing_trades: int = 0,
        total_profit: float = 0.0,
        total_loss: float = 0.0,
        largest_win: float = 0.0,
        largest_loss: float = 0.0,
        open_positions: int = 0,
        total_volume: float = 0.0
    ) -> None:
        """Update comprehensive trading statistics."""
        with self._data_lock:
            stats = self._trading_stats
            stats.total_trades = total_trades
            stats.winning_trades = winning_trades
            stats.losing_trades = losing_trades
            stats.open_positions = open_positions
            stats.total_volume = total_volume
            stats.largest_win = largest_win
            stats.largest_loss = largest_loss
            
            # Calculate derived metrics
            if total_trades > 0:
                stats.win_rate = (winning_trades / total_trades) * 100
                stats.loss_rate = (losing_trades / total_trades) * 100
            
            if winning_trades > 0:
                stats.average_win = total_profit / winning_trades
            
            if losing_trades > 0:
                stats.average_loss = abs(total_loss / losing_trades)
            
            if stats.average_loss > 0:
                stats.profit_factor = stats.average_win / stats.average_loss
            
            stats.last_trade_time = datetime.now(timezone.utc)
    
    def record_trade(self, trade_data: Dict[str, Any]) -> None:
        """Record a trade event."""
        with self._data_lock:
            trade_data["recorded_at"] = datetime.now(timezone.utc).isoformat()
            self._trade_events.append(trade_data)
            if len(self._trade_events) > self._MAX_EVENTS:
                self._trade_events = self._trade_events[-self._MAX_EVENTS:]
    
    def get_trading_stats(self) -> TradingStats:
        """Get current trading statistics."""
        with self._data_lock:
            return TradingStats(**self._trading_stats.__dict__)
    
    def get_recent_trades(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent trade events."""
        with self._data_lock:
            return list(reversed(self._trade_events[-limit:]))
    
    # -------------------------------------------------------------------------
    # STORAGE HEALTH
    # -------------------------------------------------------------------------
    
    def check_file_health(self, filename: str) -> FileHealthMetrics:
        """Check health of a specific storage file."""
        metrics = FileHealthMetrics(filename=filename, path="")
        
        # Check multiple possible locations
        possible_paths = [
            self._data_dir / filename,
            self._storage_dir / filename,
            Path(__file__).resolve().parent.parent / "bots" / "scanner_bot" / "data" / filename,
            Path(__file__).resolve().parent.parent / "bots" / "volatile_gridX" / "data" / filename,
            Path(__file__).resolve().parent.parent / "bots" / "mtb_bot" / "data" / filename,
            Path(__file__).resolve().parent.parent / "bots" / "pmb_bot" / "data" / filename,
        ]
        
        file_path = None
        for p in possible_paths:
            if p.exists():
                file_path = p
                break
        
        if not file_path:
            metrics.status = FileStatus.MISSING
            metrics.error_message = f"File not found in any expected location"
            return metrics
        
        metrics.path = str(file_path)
        
        try:
            # Basic file stats
            stat = file_path.stat()
            metrics.size_bytes = stat.st_size
            metrics.last_modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            
            # Check if file is stale (not modified in 24+ hours for active files)
            age_hours = (datetime.now(timezone.utc) - metrics.last_modified).total_seconds() / 3600
            if age_hours > 24 and filename in ["positions.json", "trade_history.json"]:
                metrics.status = FileStatus.STALE
            
            # Calculate checksum
            with open(file_path, "rb") as f:
                content = f.read()
                metrics.checksum = hashlib.md5(content).hexdigest()
            
            # Validate JSON
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    metrics.is_valid_json = True
                    
                    # Count records
                    if isinstance(data, list):
                        metrics.record_count = len(data)
                    elif isinstance(data, dict):
                        for key in ["signals", "trades", "positions", "history"]:
                            if key in data and isinstance(data[key], list):
                                metrics.record_count = len(data[key])
                                break
                        else:
                            metrics.record_count = len(data)
                    
                    metrics.status = FileStatus.HEALTHY
            except json.JSONDecodeError as e:
                metrics.status = FileStatus.CORRUPTED
                metrics.is_valid_json = False
                metrics.error_message = f"JSON parse error: {str(e)}"
            
            # Check for backup
            backup_path = file_path.with_suffix(file_path.suffix + ".bak")
            if backup_path.exists():
                metrics.backup_available = True
                backup_stat = backup_path.stat()
                metrics.backup_age_hours = (
                    datetime.now(timezone.utc) - 
                    datetime.fromtimestamp(backup_stat.st_mtime, tz=timezone.utc)
                ).total_seconds() / 3600
            
        except PermissionError:
            metrics.status = FileStatus.LOCKED
            metrics.error_message = "Permission denied"
        except Exception as e:
            metrics.status = FileStatus.CORRUPTED
            metrics.error_message = str(e)
        
        metrics.last_checked = datetime.now(timezone.utc)
        return metrics
    
    def collect_storage_health(self) -> Dict[str, FileHealthMetrics]:
        """Collect health metrics for all monitored files."""
        with self._data_lock:
            for filename in self._monitored_files:
                self._file_health[filename] = self.check_file_health(filename)
            return dict(self._file_health)
    
    def get_storage_health(self) -> Dict[str, FileHealthMetrics]:
        """Get current storage health metrics."""
        with self._data_lock:
            return dict(self._file_health)
    
    # -------------------------------------------------------------------------
    # SECURITY METRICS
    # -------------------------------------------------------------------------
    
    def record_security_event(
        self,
        event_type: str,
        user_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        details: Optional[str] = None,
        success: bool = False
    ) -> None:
        """Record a security event."""
        with self._data_lock:
            event = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "type": event_type,
                "user_id": user_id,
                "ip_address": ip_address,
                "details": details,
                "success": success,
            }
            self._security_events.append(event)
            if len(self._security_events) > self._MAX_EVENTS:
                self._security_events = self._security_events[-self._MAX_EVENTS:]
            
            # Update counters
            if event_type == "login_attempt" and not success:
                self._security_metrics.failed_login_attempts += 1
            elif event_type == "rate_limit":
                self._security_metrics.rate_limit_violations += 1
            
            self._security_metrics.last_security_event = datetime.now(timezone.utc)
    
    def add_authorized_user(self, user_id: str) -> None:
        """Add an authorized user."""
        with self._data_lock:
            if user_id not in self._security_metrics.authorized_users:
                self._security_metrics.authorized_users.append(user_id)
    
    def block_ip(self, ip_address: str) -> None:
        """Block an IP address."""
        with self._data_lock:
            if ip_address not in self._security_metrics.blocked_ips:
                self._security_metrics.blocked_ips.append(ip_address)
    
    def get_security_metrics(self) -> SecurityMetrics:
        """Get current security metrics with hourly calculations."""
        with self._data_lock:
            # Calculate hourly metrics
            one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
            today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            
            failed_last_hour = 0
            rate_limit_last_hour = 0
            events_today = 0
            
            for event in self._security_events:
                try:
                    event_time = datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))
                    if event_time >= one_hour_ago:
                        if event["type"] == "login_attempt" and not event.get("success"):
                            failed_last_hour += 1
                        elif event["type"] == "rate_limit":
                            rate_limit_last_hour += 1
                    if event_time >= today_start:
                        events_today += 1
                except (ValueError, KeyError):
                    continue
            
            metrics = SecurityMetrics(
                authorized_users=list(self._security_metrics.authorized_users),
                failed_login_attempts=self._security_metrics.failed_login_attempts,
                failed_login_last_hour=failed_last_hour,
                rate_limit_violations=self._security_metrics.rate_limit_violations,
                rate_limit_violations_last_hour=rate_limit_last_hour,
                blocked_ips=list(self._security_metrics.blocked_ips),
                last_security_event=self._security_metrics.last_security_event,
                security_events_today=events_today,
            )
            return metrics
    
    def get_security_events(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent security events."""
        with self._data_lock:
            return list(reversed(self._security_events[-limit:]))
    
    # -------------------------------------------------------------------------
    # SYSTEM METRICS
    # -------------------------------------------------------------------------
    
    def collect_system_metrics(self) -> SystemMetrics:
        """Collect current system resource metrics."""
        try:
            import psutil
            # CPU
            cpu_percent = psutil.cpu_percent(interval=None)
            cpu_count = psutil.cpu_count()

            # Memory
            memory = psutil.virtual_memory()
            memory_percent = memory.percent
            memory_used_mb = memory.used / (1024 * 1024)
            memory_total_mb = memory.total / (1024 * 1024)

            # Disk
            disk = psutil.disk_usage("/")
            disk_percent = disk.percent
            disk_used_gb = disk.used / (1024 * 1024 * 1024)
            disk_total_gb = disk.total / (1024 * 1024 * 1024)

            # Process-specific
            process = psutil.Process()
            thread_count = process.num_threads()
            try:
                open_files = len(process.open_files())
            except Exception:
                open_files = 0

            try:
                # net_connections() introduced in psutil 6.0; fall back to
                # the deprecated connections() on older installations.
                _conn_fn = getattr(process, "net_connections", None) or process.connections
                network_connections = len(_conn_fn())
            except Exception:
                network_connections = 0

            # Uptime
            uptime_seconds = time.time() - self._start_time
            days, remainder = divmod(int(uptime_seconds), 86400)
            hours, remainder = divmod(remainder, 3600)
            minutes, seconds = divmod(remainder, 60)
            uptime_formatted = f"{days}d {hours}h {minutes}m {seconds}s"

            with self._data_lock:
                self._system_metrics = SystemMetrics(
                    cpu_percent=round(cpu_percent, 1),
                    cpu_count=cpu_count,
                    memory_percent=round(memory_percent, 1),
                    memory_used_mb=round(memory_used_mb, 1),
                    memory_total_mb=round(memory_total_mb, 1),
                    disk_percent=round(disk_percent, 1),
                    disk_used_gb=round(disk_used_gb, 2),
                    disk_total_gb=round(disk_total_gb, 2),
                    uptime_seconds=round(uptime_seconds, 0),
                    uptime_formatted=uptime_formatted,
                    thread_count=thread_count,
                    open_files=open_files,
                    network_connections=network_connections,
                    process_id=process.pid,
                    last_measured=datetime.now(timezone.utc),
                )
                return SystemMetrics(**self._system_metrics.__dict__)

        except Exception:
            return SystemMetrics()
    
    def update_api_latency(self, latency_ms: float) -> None:
        """Update API latency measurement."""
        with self._data_lock:
            self._system_metrics.api_latency_ms = round(latency_ms, 2)
    
    def get_system_metrics(self) -> SystemMetrics:
        """Get current system metrics."""
        with self._data_lock:
            return SystemMetrics(**self._system_metrics.__dict__)
    
    # -------------------------------------------------------------------------
    # CUSTOM CALLBACKS
    # -------------------------------------------------------------------------
    
    def register_callback(self, name: str, callback: Callable[[], Dict[str, Any]]) -> None:
        """Register a custom metric callback."""
        with self._callback_lock:
            self._metric_callbacks[name] = callback
            logger.info("Registered metric callback: %s", name)
    
    def unregister_callback(self, name: str) -> None:
        """Unregister a custom metric callback."""
        with self._callback_lock:
            if name in self._metric_callbacks:
                del self._metric_callbacks[name]
                logger.info("Unregistered metric callback: %s", name)
    
    def collect_custom_metrics(self) -> Dict[str, Any]:
        """Collect metrics from all registered callbacks."""
        results = {}
        with self._callback_lock:
            callbacks = dict(self._metric_callbacks)
        
        for name, callback in callbacks.items():
            try:
                results[name] = callback()
            except Exception as e:
                logger.error("Callback %s failed: %s", name, e)
                results[name] = {"error": str(e)}
        
        return results
    
    # -------------------------------------------------------------------------
    # FULL COLLECTION
    # -------------------------------------------------------------------------
    
    def collect_all(self) -> Dict[str, Any]:
        """Collect all metrics at once."""
        self._last_collect_time = time.time()
        
        return {
            "safety": self.get_safety_dashboard().__dict__,
            "trading": self.get_trading_stats().__dict__,
            "system": self.collect_system_metrics().__dict__,
            "security": self.get_security_metrics().__dict__,
            "storage": {k: v.__dict__ for k, v in self.collect_storage_health().items()},
            "custom": self.collect_custom_metrics(),
            "collected_at": datetime.now(timezone.utc).isoformat(),
        }
    
    def to_dict(self) -> Dict[str, Any]:
        """Export all current metrics as dictionary."""
        with self._data_lock:
            return {
                "safety": {
                    "trading_status": self._safety_data.trading_status.value,
                    "circuit_breaker_status": self._safety_data.circuit_breaker_status.value,
                    "pnl": self._safety_data.pnl.__dict__,
                    "drawdown": self._safety_data.drawdown.__dict__,
                    "kill_switch_active": self._safety_data.kill_switch_active,
                    "emergency_stop_active": self._safety_data.emergency_stop_active,
                },
                "trading": self._trading_stats.__dict__,
                "system": self._system_metrics.__dict__,
                "security": {
                    "authorized_users": self._security_metrics.authorized_users,
                    "failed_login_attempts": self._security_metrics.failed_login_attempts,
                    "rate_limit_violations": self._security_metrics.rate_limit_violations,
                    "blocked_ips": self._security_metrics.blocked_ips,
                },
                "storage": {k: v.__dict__ for k, v in self._file_health.items()},
            }


# =============================================================================
# SINGLETON ACCESSOR
# =============================================================================

_collector_instance: Optional[MetricsCollector] = None
_collector_lock = threading.Lock()


def get_metrics_collector() -> MetricsCollector:
    """Get the singleton MetricsCollector instance."""
    global _collector_instance
    if _collector_instance is None:
        with _collector_lock:
            if _collector_instance is None:
                _collector_instance = MetricsCollector()
    return _collector_instance
