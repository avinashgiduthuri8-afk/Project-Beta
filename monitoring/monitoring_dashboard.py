"""
Monitoring Dashboard - Real-Time System Visibility
===================================================

Provides a comprehensive monitoring dashboard for PROJECT ALPHA:
- Safety status and circuit breaker visualization
- Storage health and integrity monitoring
- Security event tracking
- Trading statistics overview
- System resource monitoring

Thread-safe with automatic refresh capabilities.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Callable
from enum import Enum

from .metrics_collector import (
    get_metrics_collector,
    MetricsCollector,
    TradingStatus,
    CircuitBreakerStatus,
    FileStatus,
    SafetyDashboardData,
    TradingStats,
    SystemMetrics,
    SecurityMetrics,
    FileHealthMetrics,
)
from .health_check import (
    get_health_checker,
    HealthChecker,
    HealthStatus,
    SystemHealthReport,
)

logger = logging.getLogger("monitoring.dashboard")


# =============================================================================
# DASHBOARD PANELS
# =============================================================================

@dataclass
class SafetyPanel:
    """Safety dashboard panel data."""
    trading_status: str
    trading_status_color: str
    daily_pnl: float
    daily_pnl_percent: float
    daily_pnl_color: str
    weekly_pnl: float
    weekly_pnl_percent: float
    weekly_pnl_color: str
    monthly_pnl: float
    monthly_pnl_percent: float
    monthly_pnl_color: str
    current_drawdown: float
    drawdown_color: str
    circuit_breaker_status: str
    circuit_breaker_color: str
    kill_switch_active: bool
    emergency_stop_active: bool
    last_updated: str
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "trading_status": self.trading_status,
            "trading_status_color": self.trading_status_color,
            "daily_pnl": self.daily_pnl,
            "daily_pnl_percent": self.daily_pnl_percent,
            "daily_pnl_color": self.daily_pnl_color,
            "weekly_pnl": self.weekly_pnl,
            "weekly_pnl_percent": self.weekly_pnl_percent,
            "weekly_pnl_color": self.weekly_pnl_color,
            "monthly_pnl": self.monthly_pnl,
            "monthly_pnl_percent": self.monthly_pnl_percent,
            "monthly_pnl_color": self.monthly_pnl_color,
            "current_drawdown": self.current_drawdown,
            "drawdown_color": self.drawdown_color,
            "circuit_breaker_status": self.circuit_breaker_status,
            "circuit_breaker_color": self.circuit_breaker_color,
            "kill_switch_active": self.kill_switch_active,
            "emergency_stop_active": self.emergency_stop_active,
            "last_updated": self.last_updated,
        }


@dataclass
class StoragePanel:
    """Storage health panel data."""
    files: List[Dict[str, Any]]
    overall_status: str
    overall_status_color: str
    total_files: int
    healthy_files: int
    issues_count: int
    total_size_mb: float
    last_checked: str
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "files": self.files,
            "overall_status": self.overall_status,
            "overall_status_color": self.overall_status_color,
            "total_files": self.total_files,
            "healthy_files": self.healthy_files,
            "issues_count": self.issues_count,
            "total_size_mb": self.total_size_mb,
            "last_checked": self.last_checked,
        }


@dataclass
class SecurityPanel:
    """Security dashboard panel data."""
    authorized_users: List[str]
    authorized_users_count: int
    failed_login_attempts: int
    failed_login_last_hour: int
    failed_login_color: str
    rate_limit_violations: int
    rate_limit_violations_last_hour: int
    rate_limit_color: str
    blocked_ips: List[str]
    blocked_ips_count: int
    security_events_today: int
    recent_events: List[Dict[str, Any]]
    last_security_event: str
    overall_status: str
    overall_status_color: str
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "authorized_users": self.authorized_users,
            "authorized_users_count": self.authorized_users_count,
            "failed_login_attempts": self.failed_login_attempts,
            "failed_login_last_hour": self.failed_login_last_hour,
            "failed_login_color": self.failed_login_color,
            "rate_limit_violations": self.rate_limit_violations,
            "rate_limit_violations_last_hour": self.rate_limit_violations_last_hour,
            "rate_limit_color": self.rate_limit_color,
            "blocked_ips": self.blocked_ips,
            "blocked_ips_count": self.blocked_ips_count,
            "security_events_today": self.security_events_today,
            "recent_events": self.recent_events,
            "last_security_event": self.last_security_event,
            "overall_status": self.overall_status,
            "overall_status_color": self.overall_status_color,
        }


@dataclass
class TradingPanel:
    """Trading statistics panel data."""
    total_trades: int
    win_rate: float
    win_rate_color: str
    loss_rate: float
    profit_factor: float
    profit_factor_color: str
    average_win: float
    average_loss: float
    largest_win: float
    largest_loss: float
    max_drawdown: float
    open_positions: int
    total_volume: float
    last_trade_time: str
    recent_trades: List[Dict[str, Any]]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_trades": self.total_trades,
            "win_rate": self.win_rate,
            "win_rate_color": self.win_rate_color,
            "loss_rate": self.loss_rate,
            "profit_factor": self.profit_factor,
            "profit_factor_color": self.profit_factor_color,
            "average_win": self.average_win,
            "average_loss": self.average_loss,
            "largest_win": self.largest_win,
            "largest_loss": self.largest_loss,
            "max_drawdown": self.max_drawdown,
            "open_positions": self.open_positions,
            "total_volume": self.total_volume,
            "last_trade_time": self.last_trade_time,
            "recent_trades": self.recent_trades,
        }


@dataclass
class SystemPanel:
    """System/Railway monitoring panel data."""
    cpu_usage: float
    cpu_color: str
    memory_usage: float
    memory_used_mb: float
    memory_total_mb: float
    memory_color: str
    disk_usage: float
    disk_used_gb: float
    disk_total_gb: float
    disk_color: str
    uptime: str
    uptime_seconds: float
    thread_count: int
    open_files: int
    network_connections: int
    api_latency_ms: float
    api_latency_color: str
    process_id: int
    last_measured: str
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "cpu_usage": self.cpu_usage,
            "cpu_color": self.cpu_color,
            "memory_usage": self.memory_usage,
            "memory_used_mb": self.memory_used_mb,
            "memory_total_mb": self.memory_total_mb,
            "memory_color": self.memory_color,
            "disk_usage": self.disk_usage,
            "disk_used_gb": self.disk_used_gb,
            "disk_total_gb": self.disk_total_gb,
            "disk_color": self.disk_color,
            "uptime": self.uptime,
            "uptime_seconds": self.uptime_seconds,
            "thread_count": self.thread_count,
            "open_files": self.open_files,
            "network_connections": self.network_connections,
            "api_latency_ms": self.api_latency_ms,
            "api_latency_color": self.api_latency_color,
            "process_id": self.process_id,
            "last_measured": self.last_measured,
        }


# =============================================================================
# MONITORING DASHBOARD
# =============================================================================

class MonitoringDashboard:
    """
    Comprehensive monitoring dashboard aggregator.
    
    Collects data from MetricsCollector and HealthChecker to provide
    a unified dashboard view with color-coded status indicators.
    """
    
    # Color constants
    COLOR_GREEN = "#22c55e"
    COLOR_YELLOW = "#eab308"
    COLOR_RED = "#ef4444"
    COLOR_GRAY = "#6b7280"
    COLOR_BLUE = "#3b82f6"
    
    def __init__(
        self,
        collector: Optional[MetricsCollector] = None,
        health_checker: Optional[HealthChecker] = None,
    ):
        self._collector = collector or get_metrics_collector()
        self._health_checker = health_checker or get_health_checker()
        self._lock = threading.RLock()
        self._cached_dashboard: Optional[Dict[str, Any]] = None
        self._cache_time: float = 0
        self._cache_ttl: float = 5.0  # 5 second cache
        
        logger.info("MonitoringDashboard initialized")
    
    # -------------------------------------------------------------------------
    # COLOR HELPERS
    # -------------------------------------------------------------------------
    
    def _pnl_color(self, value: float) -> str:
        """Get color for PnL value."""
        if value > 0:
            return self.COLOR_GREEN
        elif value < 0:
            return self.COLOR_RED
        return self.COLOR_GRAY
    
    def _percentage_color(self, value: float, warning: float, critical: float) -> str:
        """Get color for percentage value with thresholds."""
        if value >= critical:
            return self.COLOR_RED
        elif value >= warning:
            return self.COLOR_YELLOW
        return self.COLOR_GREEN
    
    def _status_color(self, status: str) -> str:
        """Get color for status string."""
        status_upper = status.upper()
        if status_upper in ("ACTIVE", "HEALTHY", "CLOSED", "OK"):
            return self.COLOR_GREEN
        elif status_upper in ("PAUSED", "DEGRADED", "HALF_OPEN", "WARNING"):
            return self.COLOR_YELLOW
        elif status_upper in ("EMERGENCY", "UNHEALTHY", "OPEN", "CRITICAL"):
            return self.COLOR_RED
        return self.COLOR_GRAY
    
    def _win_rate_color(self, rate: float) -> str:
        """Get color for win rate."""
        if rate >= 60:
            return self.COLOR_GREEN
        elif rate >= 50:
            return self.COLOR_YELLOW
        return self.COLOR_RED
    
    def _profit_factor_color(self, pf: float) -> str:
        """Get color for profit factor."""
        if pf >= 2.0:
            return self.COLOR_GREEN
        elif pf >= 1.0:
            return self.COLOR_YELLOW
        return self.COLOR_RED
    
    # -------------------------------------------------------------------------
    # PANEL BUILDERS
    # -------------------------------------------------------------------------
    
    def build_safety_panel(self) -> SafetyPanel:
        """Build safety dashboard panel."""
        safety = self._collector.get_safety_dashboard()
        
        return SafetyPanel(
            trading_status=safety.trading_status.value,
            trading_status_color=self._status_color(safety.trading_status.value),
            daily_pnl=round(safety.pnl.daily_pnl, 2),
            daily_pnl_percent=round(safety.pnl.daily_pnl_percent, 2),
            daily_pnl_color=self._pnl_color(safety.pnl.daily_pnl),
            weekly_pnl=round(safety.pnl.weekly_pnl, 2),
            weekly_pnl_percent=round(safety.pnl.weekly_pnl_percent, 2),
            weekly_pnl_color=self._pnl_color(safety.pnl.weekly_pnl),
            monthly_pnl=round(safety.pnl.monthly_pnl, 2),
            monthly_pnl_percent=round(safety.pnl.monthly_pnl_percent, 2),
            monthly_pnl_color=self._pnl_color(safety.pnl.monthly_pnl),
            current_drawdown=round(safety.drawdown.current_drawdown_percent, 2),
            drawdown_color=self._percentage_color(safety.drawdown.current_drawdown_percent, 10, 20),
            circuit_breaker_status=safety.circuit_breaker_status.value,
            circuit_breaker_color=self._status_color(safety.circuit_breaker_status.value),
            kill_switch_active=safety.kill_switch_active,
            emergency_stop_active=safety.emergency_stop_active,
            last_updated=safety.last_updated.isoformat() if safety.last_updated else "Never",
        )
    
    def build_storage_panel(self) -> StoragePanel:
        """Build storage health panel."""
        storage_health = self._collector.collect_storage_health()
        
        files = []
        total_size = 0
        healthy_count = 0
        issues_count = 0
        
        for filename, metrics in storage_health.items():
            file_data = {
                "filename": metrics.filename,
                "path": metrics.path,
                "status": metrics.status.value,
                "status_color": self._status_color(metrics.status.value),
                "size_bytes": metrics.size_bytes,
                "size_formatted": self._format_size(metrics.size_bytes),
                "checksum": metrics.checksum[:8] + "..." if metrics.checksum else "N/A",
                "record_count": metrics.record_count,
                "is_valid_json": metrics.is_valid_json,
                "backup_available": metrics.backup_available,
                "backup_age_hours": round(metrics.backup_age_hours, 1),
                "last_modified": metrics.last_modified.isoformat() if metrics.last_modified else "N/A",
                "error_message": metrics.error_message,
            }
            files.append(file_data)
            total_size += metrics.size_bytes
            
            if metrics.status == FileStatus.HEALTHY:
                healthy_count += 1
            elif metrics.status != FileStatus.MISSING:
                issues_count += 1
        
        # Determine overall status
        if issues_count > 0:
            overall_status = "DEGRADED"
        elif healthy_count == len(files):
            overall_status = "HEALTHY"
        else:
            overall_status = "WARNING"
        
        return StoragePanel(
            files=files,
            overall_status=overall_status,
            overall_status_color=self._status_color(overall_status),
            total_files=len(files),
            healthy_files=healthy_count,
            issues_count=issues_count,
            total_size_mb=round(total_size / (1024 * 1024), 2),
            last_checked=datetime.now(timezone.utc).isoformat(),
        )
    
    def build_security_panel(self) -> SecurityPanel:
        """Build security dashboard panel."""
        security = self._collector.get_security_metrics()
        events = self._collector.get_security_events(limit=10)
        
        # Determine overall status
        if security.failed_login_last_hour >= 10 or security.rate_limit_violations_last_hour >= 50:
            overall_status = "WARNING"
        elif security.failed_login_last_hour >= 20 or len(security.blocked_ips) > 20:
            overall_status = "CRITICAL"
        else:
            overall_status = "HEALTHY"
        
        return SecurityPanel(
            authorized_users=security.authorized_users[:10],  # Limit display
            authorized_users_count=len(security.authorized_users),
            failed_login_attempts=security.failed_login_attempts,
            failed_login_last_hour=security.failed_login_last_hour,
            failed_login_color=self._percentage_color(security.failed_login_last_hour, 5, 10),
            rate_limit_violations=security.rate_limit_violations,
            rate_limit_violations_last_hour=security.rate_limit_violations_last_hour,
            rate_limit_color=self._percentage_color(security.rate_limit_violations_last_hour, 20, 50),
            blocked_ips=security.blocked_ips[:10],
            blocked_ips_count=len(security.blocked_ips),
            security_events_today=security.security_events_today,
            recent_events=events,
            last_security_event=security.last_security_event.isoformat() if security.last_security_event else "None",
            overall_status=overall_status,
            overall_status_color=self._status_color(overall_status),
        )
    
    def build_trading_panel(self) -> TradingPanel:
        """Build trading statistics panel."""
        stats = self._collector.get_trading_stats()
        recent_trades = self._collector.get_recent_trades(limit=10)
        
        return TradingPanel(
            total_trades=stats.total_trades,
            win_rate=round(stats.win_rate, 1),
            win_rate_color=self._win_rate_color(stats.win_rate),
            loss_rate=round(stats.loss_rate, 1),
            profit_factor=round(stats.profit_factor, 2),
            profit_factor_color=self._profit_factor_color(stats.profit_factor),
            average_win=round(stats.average_win, 2),
            average_loss=round(stats.average_loss, 2),
            largest_win=round(stats.largest_win, 2),
            largest_loss=round(stats.largest_loss, 2),
            max_drawdown=round(stats.max_drawdown, 2),
            open_positions=stats.open_positions,
            total_volume=round(stats.total_volume, 2),
            last_trade_time=stats.last_trade_time.isoformat() if stats.last_trade_time else "Never",
            recent_trades=recent_trades,
        )
    
    def build_system_panel(self) -> SystemPanel:
        """Build system/Railway monitoring panel."""
        metrics = self._collector.collect_system_metrics()
        
        return SystemPanel(
            cpu_usage=metrics.cpu_percent,
            cpu_color=self._percentage_color(metrics.cpu_percent, 70, 90),
            memory_usage=metrics.memory_percent,
            memory_used_mb=metrics.memory_used_mb,
            memory_total_mb=metrics.memory_total_mb,
            memory_color=self._percentage_color(metrics.memory_percent, 75, 90),
            disk_usage=metrics.disk_percent,
            disk_used_gb=metrics.disk_used_gb,
            disk_total_gb=metrics.disk_total_gb,
            disk_color=self._percentage_color(metrics.disk_percent, 80, 95),
            uptime=metrics.uptime_formatted,
            uptime_seconds=metrics.uptime_seconds,
            thread_count=metrics.thread_count,
            open_files=metrics.open_files,
            network_connections=metrics.network_connections,
            api_latency_ms=metrics.api_latency_ms,
            api_latency_color=self._percentage_color(metrics.api_latency_ms / 10, 50, 200),  # Scale for color
            process_id=metrics.process_id,
            last_measured=metrics.last_measured.isoformat() if metrics.last_measured else "Never",
        )
    
    # -------------------------------------------------------------------------
    # FULL DASHBOARD
    # -------------------------------------------------------------------------
    
    def get_dashboard(self, use_cache: bool = True) -> Dict[str, Any]:
        """Get complete dashboard data with optional caching."""
        with self._lock:
            now = time.time()
            
            # Return cached if valid
            if use_cache and self._cached_dashboard and (now - self._cache_time) < self._cache_ttl:
                return self._cached_dashboard
            
            # Build fresh dashboard
            dashboard = {
                "safety": self.build_safety_panel().to_dict(),
                "storage": self.build_storage_panel().to_dict(),
                "security": self.build_security_panel().to_dict(),
                "trading": self.build_trading_panel().to_dict(),
                "system": self.build_system_panel().to_dict(),
                "health_report": self._health_checker.run_all_checks().to_dict(),
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }
            
            # Update cache
            self._cached_dashboard = dashboard
            self._cache_time = now
            
            return dashboard
    
    def get_dashboard_summary(self) -> Dict[str, Any]:
        """Get a lightweight summary of dashboard status."""
        safety = self._collector.get_safety_dashboard()
        security = self._collector.get_security_metrics()
        system = self._collector.get_system_metrics()
        
        status, message = self._health_checker.quick_check()
        
        return {
            "overall_health": status.value,
            "overall_health_color": self._status_color(status.value),
            "message": message,
            "trading_status": safety.trading_status.value,
            "circuit_breaker": safety.circuit_breaker_status.value,
            "daily_pnl_percent": round(safety.pnl.daily_pnl_percent, 2),
            "current_drawdown": round(safety.drawdown.current_drawdown_percent, 2),
            "cpu_percent": system.cpu_percent,
            "memory_percent": system.memory_percent,
            "uptime": system.uptime_formatted,
            "security_events_today": security.security_events_today,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    
    # -------------------------------------------------------------------------
    # HTML RENDERING
    # -------------------------------------------------------------------------
    
    def render_html(self) -> str:
        """Render dashboard as HTML."""
        dashboard = self.get_dashboard()
        safety = dashboard["safety"]
        storage = dashboard["storage"]
        security = dashboard["security"]
        trading = dashboard["trading"]
        system = dashboard["system"]
        
        html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PROJECT ALPHA - Monitoring Dashboard</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #e2e8f0; padding: 20px; }}
        .dashboard {{ max-width: 1600px; margin: 0 auto; }}
        h1 {{ text-align: center; margin-bottom: 20px; color: #f1f5f9; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(400px, 1fr)); gap: 20px; }}
        .panel {{ background: #1e293b; border-radius: 12px; padding: 20px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }}
        .panel-title {{ font-size: 1.2rem; font-weight: 600; margin-bottom: 15px; padding-bottom: 10px; border-bottom: 1px solid #334155; }}
        .metric {{ display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid #334155; }}
        .metric:last-child {{ border-bottom: none; }}
        .metric-label {{ color: #94a3b8; }}
        .metric-value {{ font-weight: 600; }}
        .status-badge {{ padding: 4px 12px; border-radius: 20px; font-size: 0.85rem; font-weight: 600; }}
        .status-green {{ background: rgba(34, 197, 94, 0.2); color: #22c55e; }}
        .status-yellow {{ background: rgba(234, 179, 8, 0.2); color: #eab308; }}
        .status-red {{ background: rgba(239, 68, 68, 0.2); color: #ef4444; }}
        .status-gray {{ background: rgba(107, 114, 128, 0.2); color: #6b7280; }}
        .file-list {{ max-height: 200px; overflow-y: auto; }}
        .file-item {{ padding: 8px; background: #334155; border-radius: 6px; margin-bottom: 8px; font-size: 0.9rem; }}
        .progress-bar {{ height: 8px; background: #334155; border-radius: 4px; overflow: hidden; margin-top: 4px; }}
        .progress-fill {{ height: 100%; border-radius: 4px; transition: width 0.3s; }}
        .refresh-btn {{ position: fixed; bottom: 20px; right: 20px; padding: 12px 24px; background: #3b82f6; color: white; border: none; border-radius: 8px; cursor: pointer; font-size: 1rem; }}
        .refresh-btn:hover {{ background: #2563eb; }}
        .timestamp {{ text-align: center; color: #64748b; font-size: 0.85rem; margin-top: 20px; }}
    </style>
</head>
<body>
    <div class="dashboard">
        <h1>PROJECT ALPHA - Production Monitoring</h1>
        
        <div class="grid">
            <!-- Safety Panel -->
            <div class="panel">
                <div class="panel-title">Safety Dashboard</div>
                <div class="metric">
                    <span class="metric-label">Trading Status</span>
                    <span class="status-badge" style="background: {safety['trading_status_color']}20; color: {safety['trading_status_color']}">{safety['trading_status']}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Daily PnL</span>
                    <span class="metric-value" style="color: {safety['daily_pnl_color']}">${safety['daily_pnl']:,.2f} ({safety['daily_pnl_percent']:+.2f}%)</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Weekly PnL</span>
                    <span class="metric-value" style="color: {safety['weekly_pnl_color']}">${safety['weekly_pnl']:,.2f} ({safety['weekly_pnl_percent']:+.2f}%)</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Monthly PnL</span>
                    <span class="metric-value" style="color: {safety['monthly_pnl_color']}">${safety['monthly_pnl']:,.2f} ({safety['monthly_pnl_percent']:+.2f}%)</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Current Drawdown</span>
                    <span class="metric-value" style="color: {safety['drawdown_color']}">{safety['current_drawdown']:.2f}%</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Circuit Breaker</span>
                    <span class="status-badge" style="background: {safety['circuit_breaker_color']}20; color: {safety['circuit_breaker_color']}">{safety['circuit_breaker_status']}</span>
                </div>
            </div>
            
            <!-- Storage Panel -->
            <div class="panel">
                <div class="panel-title">Storage Health</div>
                <div class="metric">
                    <span class="metric-label">Overall Status</span>
                    <span class="status-badge" style="background: {storage['overall_status_color']}20; color: {storage['overall_status_color']}">{storage['overall_status']}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Files</span>
                    <span class="metric-value">{storage['healthy_files']}/{storage['total_files']} healthy</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Total Size</span>
                    <span class="metric-value">{storage['total_size_mb']:.2f} MB</span>
                </div>
                <div class="file-list">
                    {''.join([f'<div class="file-item"><span style="color: {f["status_color"]}">[{f["status"]}]</span> {f["filename"]} ({f["record_count"]} records)</div>' for f in storage['files']])}
                </div>
            </div>
            
            <!-- Security Panel -->
            <div class="panel">
                <div class="panel-title">Security Dashboard</div>
                <div class="metric">
                    <span class="metric-label">Status</span>
                    <span class="status-badge" style="background: {security['overall_status_color']}20; color: {security['overall_status_color']}">{security['overall_status']}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Authorized Users</span>
                    <span class="metric-value">{security['authorized_users_count']}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Failed Logins (1h)</span>
                    <span class="metric-value" style="color: {security['failed_login_color']}">{security['failed_login_last_hour']}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Rate Limit Violations (1h)</span>
                    <span class="metric-value" style="color: {security['rate_limit_color']}">{security['rate_limit_violations_last_hour']}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Events Today</span>
                    <span class="metric-value">{security['security_events_today']}</span>
                </div>
            </div>
            
            <!-- Trading Panel -->
            <div class="panel">
                <div class="panel-title">Trading Statistics</div>
                <div class="metric">
                    <span class="metric-label">Total Trades</span>
                    <span class="metric-value">{trading['total_trades']}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Win Rate</span>
                    <span class="metric-value" style="color: {trading['win_rate_color']}">{trading['win_rate']:.1f}%</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Profit Factor</span>
                    <span class="metric-value" style="color: {trading['profit_factor_color']}">{trading['profit_factor']:.2f}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Avg Win / Loss</span>
                    <span class="metric-value">${trading['average_win']:,.2f} / ${trading['average_loss']:,.2f}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Largest Win / Loss</span>
                    <span class="metric-value">${trading['largest_win']:,.2f} / ${trading['largest_loss']:,.2f}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Open Positions</span>
                    <span class="metric-value">{trading['open_positions']}</span>
                </div>
            </div>
            
            <!-- System Panel -->
            <div class="panel">
                <div class="panel-title">Railway Monitoring</div>
                <div class="metric">
                    <span class="metric-label">CPU Usage</span>
                    <span class="metric-value" style="color: {system['cpu_color']}">{system['cpu_usage']:.1f}%</span>
                </div>
                <div class="progress-bar"><div class="progress-fill" style="width: {system['cpu_usage']}%; background: {system['cpu_color']}"></div></div>
                <div class="metric">
                    <span class="metric-label">Memory Usage</span>
                    <span class="metric-value" style="color: {system['memory_color']}">{system['memory_usage']:.1f}% ({system['memory_used_mb']:.0f}/{system['memory_total_mb']:.0f} MB)</span>
                </div>
                <div class="progress-bar"><div class="progress-fill" style="width: {system['memory_usage']}%; background: {system['memory_color']}"></div></div>
                <div class="metric">
                    <span class="metric-label">Disk Usage</span>
                    <span class="metric-value" style="color: {system['disk_color']}">{system['disk_usage']:.1f}% ({system['disk_used_gb']:.1f}/{system['disk_total_gb']:.1f} GB)</span>
                </div>
                <div class="progress-bar"><div class="progress-fill" style="width: {system['disk_usage']}%; background: {system['disk_color']}"></div></div>
                <div class="metric">
                    <span class="metric-label">Uptime</span>
                    <span class="metric-value">{system['uptime']}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Threads</span>
                    <span class="metric-value">{system['thread_count']}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">API Latency</span>
                    <span class="metric-value" style="color: {system['api_latency_color']}">{system['api_latency_ms']:.0f} ms</span>
                </div>
            </div>
        </div>
        
        <div class="timestamp">Last updated: {dashboard['generated_at']}</div>
    </div>
    
    <button class="refresh-btn" onclick="location.reload()">Refresh</button>
    
    <script>
        // Auto-refresh every 30 seconds
        setTimeout(() => location.reload(), 30000);
    </script>
</body>
</html>
"""
        return html
    
    # -------------------------------------------------------------------------
    # UTILITIES
    # -------------------------------------------------------------------------
    
    @staticmethod
    def _format_size(size_bytes: int) -> str:
        """Format bytes to human readable string."""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"
