"""
Health Check System - Comprehensive System Health Verification
==============================================================

Provides structured health checks for all PROJECT ALPHA components:
- Storage integrity verification
- Service availability checks  
- Circuit breaker status
- API endpoint health
- Database/file connectivity

Thread-safe with configurable thresholds and alerting.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from .metrics_collector import (
    get_metrics_collector,
    FileStatus,
    TradingStatus,
    CircuitBreakerStatus,
)

logger = logging.getLogger("monitoring.health")


# =============================================================================
# ENUMS AND CONSTANTS
# =============================================================================

class HealthStatus(Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    UNKNOWN = "UNKNOWN"


class CheckSeverity(Enum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"
    INFO = "INFO"


# Thresholds
DEFAULT_THRESHOLDS = {
    "cpu_warning": 70.0,
    "cpu_critical": 90.0,
    "memory_warning": 75.0,
    "memory_critical": 90.0,
    "disk_warning": 80.0,
    "disk_critical": 95.0,
    "api_latency_warning_ms": 500,
    "api_latency_critical_ms": 2000,
    "file_stale_hours": 24,
    "max_failed_logins_hour": 10,
    "max_rate_limit_hour": 50,
}


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class HealthCheckResult:
    """Result of a single health check."""
    name: str
    status: HealthStatus
    severity: CheckSeverity
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0
    checked_at: Optional[datetime] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "severity": self.severity.value,
            "message": self.message,
            "details": self.details,
            "duration_ms": self.duration_ms,
            "checked_at": self.checked_at.isoformat() if self.checked_at else None,
        }


@dataclass
class SystemHealthReport:
    """Comprehensive system health report."""
    overall_status: HealthStatus
    checks: List[HealthCheckResult]
    summary: Dict[str, int]
    critical_issues: List[str]
    warnings: List[str]
    generated_at: datetime
    duration_ms: float
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "overall_status": self.overall_status.value,
            "checks": [c.to_dict() for c in self.checks],
            "summary": self.summary,
            "critical_issues": self.critical_issues,
            "warnings": self.warnings,
            "generated_at": self.generated_at.isoformat(),
            "duration_ms": self.duration_ms,
        }


# =============================================================================
# HEALTH CHECKER - SINGLETON
# =============================================================================

class HealthChecker:
    """
    Thread-safe health checker singleton.
    
    Performs comprehensive health checks on all system components
    with configurable thresholds and alerting callbacks.
    """
    
    _instance: Optional["HealthChecker"] = None
    _lock = threading.Lock()
    
    def __new__(cls) -> "HealthChecker":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._check_lock = threading.RLock()
        self._thresholds = dict(DEFAULT_THRESHOLDS)
        self._alert_callbacks: List[Callable[[HealthCheckResult], None]] = []
        self._last_report: Optional[SystemHealthReport] = None
        self._check_history: List[SystemHealthReport] = []
        self._MAX_HISTORY = 100
        
        # File checksum cache for corruption detection
        self._checksum_cache: Dict[str, str] = {}
        
        self._initialized = True
        logger.info("HealthChecker initialized")
    
    def set_threshold(self, key: str, value: float) -> None:
        """Set a specific threshold value."""
        with self._check_lock:
            self._thresholds[key] = value
    
    def get_thresholds(self) -> Dict[str, float]:
        """Get current thresholds."""
        with self._check_lock:
            return dict(self._thresholds)
    
    def register_alert_callback(self, callback: Callable[[HealthCheckResult], None]) -> None:
        """Register callback for health alerts."""
        with self._check_lock:
            self._alert_callbacks.append(callback)
    
    def _trigger_alerts(self, result: HealthCheckResult) -> None:
        """Trigger alert callbacks for unhealthy checks."""
        if result.status in (HealthStatus.UNHEALTHY, HealthStatus.DEGRADED):
            for callback in self._alert_callbacks:
                try:
                    callback(result)
                except Exception as e:
                    logger.error("Alert callback failed: %s", e)
    
    # -------------------------------------------------------------------------
    # INDIVIDUAL HEALTH CHECKS
    # -------------------------------------------------------------------------
    
    def check_storage_file(self, filename: str, path: Optional[Path] = None) -> HealthCheckResult:
        """Check health of a specific storage file."""
        start = time.time()
        
        # Find file
        if path and path.exists():
            file_path = path
        else:
            base_paths = [
                Path(__file__).resolve().parent.parent / "data",
                Path(__file__).resolve().parent.parent / "storage",
                Path(__file__).resolve().parent.parent / "bots" / "scanner_bot" / "data",
                Path(__file__).resolve().parent.parent / "bots" / "volatile_gridX" / "data",
            ]
            file_path = None
            for base in base_paths:
                candidate = base / filename
                if candidate.exists():
                    file_path = candidate
                    break
        
        if not file_path or not file_path.exists():
            return HealthCheckResult(
                name=f"storage_{filename}",
                status=HealthStatus.UNHEALTHY,
                severity=CheckSeverity.CRITICAL,
                message=f"File not found: {filename}",
                details={"filename": filename, "searched_paths": [str(p) for p in base_paths]},
                duration_ms=(time.time() - start) * 1000,
                checked_at=datetime.now(timezone.utc),
            )
        
        try:
            # Read and validate
            stat = file_path.stat()
            size_bytes = stat.st_size
            modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - modified).total_seconds() / 3600
            
            # Calculate checksum
            with open(file_path, "rb") as f:
                content = f.read()
                checksum = hashlib.md5(content).hexdigest()
            
            # Detect corruption via checksum change without expected modification
            previous_checksum = self._checksum_cache.get(filename)
            checksum_changed = previous_checksum and previous_checksum != checksum
            self._checksum_cache[filename] = checksum
            
            # Validate JSON
            is_valid = False
            record_count = 0
            error_msg = ""
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    is_valid = True
                    if isinstance(data, list):
                        record_count = len(data)
                    elif isinstance(data, dict):
                        for key in ["signals", "trades", "positions", "history"]:
                            if key in data and isinstance(data[key], list):
                                record_count = len(data[key])
                                break
            except json.JSONDecodeError as e:
                error_msg = str(e)
            
            # Check backup
            backup_path = file_path.with_suffix(file_path.suffix + ".bak")
            backup_exists = backup_path.exists()
            
            # Determine status
            details = {
                "filename": filename,
                "path": str(file_path),
                "size_bytes": size_bytes,
                "checksum": checksum,
                "age_hours": round(age_hours, 1),
                "is_valid_json": is_valid,
                "record_count": record_count,
                "backup_available": backup_exists,
                "checksum_changed": checksum_changed,
            }
            
            if not is_valid:
                status = HealthStatus.UNHEALTHY
                severity = CheckSeverity.CRITICAL
                message = f"Corrupted JSON: {error_msg}"
            elif age_hours > self._thresholds["file_stale_hours"] and filename in ["positions.json", "trade_history.json"]:
                status = HealthStatus.DEGRADED
                severity = CheckSeverity.WARNING
                message = f"File is stale ({age_hours:.1f} hours old)"
            elif size_bytes == 0:
                status = HealthStatus.DEGRADED
                severity = CheckSeverity.WARNING
                message = "File is empty"
            else:
                status = HealthStatus.HEALTHY
                severity = CheckSeverity.INFO
                message = f"Healthy ({record_count} records, {size_bytes} bytes)"
            
            return HealthCheckResult(
                name=f"storage_{filename}",
                status=status,
                severity=severity,
                message=message,
                details=details,
                duration_ms=(time.time() - start) * 1000,
                checked_at=datetime.now(timezone.utc),
            )
        
        except PermissionError:
            return HealthCheckResult(
                name=f"storage_{filename}",
                status=HealthStatus.UNHEALTHY,
                severity=CheckSeverity.CRITICAL,
                message="Permission denied",
                details={"filename": filename, "path": str(file_path)},
                duration_ms=(time.time() - start) * 1000,
                checked_at=datetime.now(timezone.utc),
            )
        except Exception as e:
            return HealthCheckResult(
                name=f"storage_{filename}",
                status=HealthStatus.UNHEALTHY,
                severity=CheckSeverity.CRITICAL,
                message=f"Error: {str(e)}",
                details={"filename": filename, "error": str(e)},
                duration_ms=(time.time() - start) * 1000,
                checked_at=datetime.now(timezone.utc),
            )
    
    def check_circuit_breaker(self) -> HealthCheckResult:
        """Check circuit breaker status."""
        start = time.time()
        collector = get_metrics_collector()
        safety = collector.get_safety_dashboard()
        
        status_map = {
            CircuitBreakerStatus.CLOSED: (HealthStatus.HEALTHY, CheckSeverity.INFO, "Normal operation"),
            CircuitBreakerStatus.HALF_OPEN: (HealthStatus.DEGRADED, CheckSeverity.WARNING, "Testing recovery"),
            CircuitBreakerStatus.OPEN: (HealthStatus.UNHEALTHY, CheckSeverity.CRITICAL, "Trading halted"),
        }
        
        health_status, severity, message = status_map.get(
            safety.circuit_breaker_status,
            (HealthStatus.UNKNOWN, CheckSeverity.WARNING, "Unknown status")
        )
        
        return HealthCheckResult(
            name="circuit_breaker",
            status=health_status,
            severity=severity,
            message=message,
            details={
                "circuit_breaker_status": safety.circuit_breaker_status.value,
                "kill_switch_active": safety.kill_switch_active,
                "emergency_stop_active": safety.emergency_stop_active,
            },
            duration_ms=(time.time() - start) * 1000,
            checked_at=datetime.now(timezone.utc),
        )
    
    def check_trading_status(self) -> HealthCheckResult:
        """Check trading status."""
        start = time.time()
        collector = get_metrics_collector()
        safety = collector.get_safety_dashboard()
        
        status_map = {
            TradingStatus.ACTIVE: (HealthStatus.HEALTHY, CheckSeverity.INFO, "Trading active"),
            TradingStatus.PAUSED: (HealthStatus.DEGRADED, CheckSeverity.WARNING, "Trading paused"),
            TradingStatus.EMERGENCY: (HealthStatus.UNHEALTHY, CheckSeverity.CRITICAL, "Emergency stop active"),
            TradingStatus.UNKNOWN: (HealthStatus.UNKNOWN, CheckSeverity.WARNING, "Status unknown"),
        }
        
        health_status, severity, message = status_map.get(
            safety.trading_status,
            (HealthStatus.UNKNOWN, CheckSeverity.WARNING, "Unknown status")
        )
        
        return HealthCheckResult(
            name="trading_status",
            status=health_status,
            severity=severity,
            message=message,
            details={
                "trading_status": safety.trading_status.value,
                "pnl_daily": safety.pnl.daily_pnl,
                "drawdown_percent": safety.drawdown.current_drawdown_percent,
            },
            duration_ms=(time.time() - start) * 1000,
            checked_at=datetime.now(timezone.utc),
        )
    
    def check_system_resources(self) -> HealthCheckResult:
        """Check system resource utilization."""
        start = time.time()
        collector = get_metrics_collector()
        metrics = collector.collect_system_metrics()
        
        issues = []
        severity = CheckSeverity.INFO
        status = HealthStatus.HEALTHY
        
        # CPU check
        if metrics.cpu_percent >= self._thresholds["cpu_critical"]:
            issues.append(f"CPU critical: {metrics.cpu_percent}%")
            status = HealthStatus.UNHEALTHY
            severity = CheckSeverity.CRITICAL
        elif metrics.cpu_percent >= self._thresholds["cpu_warning"]:
            issues.append(f"CPU high: {metrics.cpu_percent}%")
            if status == HealthStatus.HEALTHY:
                status = HealthStatus.DEGRADED
                severity = CheckSeverity.WARNING
        
        # Memory check
        if metrics.memory_percent >= self._thresholds["memory_critical"]:
            issues.append(f"Memory critical: {metrics.memory_percent}%")
            status = HealthStatus.UNHEALTHY
            severity = CheckSeverity.CRITICAL
        elif metrics.memory_percent >= self._thresholds["memory_warning"]:
            issues.append(f"Memory high: {metrics.memory_percent}%")
            if status == HealthStatus.HEALTHY:
                status = HealthStatus.DEGRADED
                severity = CheckSeverity.WARNING
        
        # Disk check
        if metrics.disk_percent >= self._thresholds["disk_critical"]:
            issues.append(f"Disk critical: {metrics.disk_percent}%")
            status = HealthStatus.UNHEALTHY
            severity = CheckSeverity.CRITICAL
        elif metrics.disk_percent >= self._thresholds["disk_warning"]:
            issues.append(f"Disk high: {metrics.disk_percent}%")
            if status == HealthStatus.HEALTHY:
                status = HealthStatus.DEGRADED
                severity = CheckSeverity.WARNING
        
        message = "; ".join(issues) if issues else f"All resources OK (CPU: {metrics.cpu_percent}%, Mem: {metrics.memory_percent}%, Disk: {metrics.disk_percent}%)"
        
        return HealthCheckResult(
            name="system_resources",
            status=status,
            severity=severity,
            message=message,
            details={
                "cpu_percent": metrics.cpu_percent,
                "memory_percent": metrics.memory_percent,
                "memory_used_mb": metrics.memory_used_mb,
                "disk_percent": metrics.disk_percent,
                "thread_count": metrics.thread_count,
                "uptime_seconds": metrics.uptime_seconds,
            },
            duration_ms=(time.time() - start) * 1000,
            checked_at=datetime.now(timezone.utc),
        )
    
    def check_security(self) -> HealthCheckResult:
        """Check security metrics."""
        start = time.time()
        collector = get_metrics_collector()
        security = collector.get_security_metrics()
        
        issues = []
        severity = CheckSeverity.INFO
        status = HealthStatus.HEALTHY
        
        # Failed logins
        if security.failed_login_last_hour >= self._thresholds["max_failed_logins_hour"]:
            issues.append(f"High failed logins: {security.failed_login_last_hour}/hour")
            status = HealthStatus.DEGRADED
            severity = CheckSeverity.WARNING
        
        # Rate limits
        if security.rate_limit_violations_last_hour >= self._thresholds["max_rate_limit_hour"]:
            issues.append(f"High rate limit violations: {security.rate_limit_violations_last_hour}/hour")
            if status == HealthStatus.HEALTHY:
                status = HealthStatus.DEGRADED
                severity = CheckSeverity.WARNING
        
        # Blocked IPs (many blocked IPs could indicate attack)
        if len(security.blocked_ips) > 10:
            issues.append(f"Many blocked IPs: {len(security.blocked_ips)}")
            if status == HealthStatus.HEALTHY:
                status = HealthStatus.DEGRADED
                severity = CheckSeverity.WARNING
        
        message = "; ".join(issues) if issues else f"Security OK ({len(security.authorized_users)} authorized users)"
        
        return HealthCheckResult(
            name="security",
            status=status,
            severity=severity,
            message=message,
            details={
                "authorized_users_count": len(security.authorized_users),
                "failed_login_attempts": security.failed_login_attempts,
                "failed_login_last_hour": security.failed_login_last_hour,
                "rate_limit_violations": security.rate_limit_violations,
                "rate_limit_violations_last_hour": security.rate_limit_violations_last_hour,
                "blocked_ips_count": len(security.blocked_ips),
                "security_events_today": security.security_events_today,
            },
            duration_ms=(time.time() - start) * 1000,
            checked_at=datetime.now(timezone.utc),
        )
    
    def check_api_endpoint(self, url: str, timeout: float = 5.0) -> HealthCheckResult:
        """Check API endpoint availability and latency."""
        start = time.time()
        
        try:
            response = requests.get(url, timeout=timeout)
            latency_ms = (time.time() - start) * 1000
            
            collector = get_metrics_collector()
            collector.update_api_latency(latency_ms)
            
            if response.status_code == 200:
                if latency_ms >= self._thresholds["api_latency_critical_ms"]:
                    status = HealthStatus.DEGRADED
                    severity = CheckSeverity.WARNING
                    message = f"High latency: {latency_ms:.0f}ms"
                elif latency_ms >= self._thresholds["api_latency_warning_ms"]:
                    status = HealthStatus.DEGRADED
                    severity = CheckSeverity.WARNING
                    message = f"Elevated latency: {latency_ms:.0f}ms"
                else:
                    status = HealthStatus.HEALTHY
                    severity = CheckSeverity.INFO
                    message = f"OK ({latency_ms:.0f}ms)"
            else:
                status = HealthStatus.UNHEALTHY
                severity = CheckSeverity.CRITICAL
                message = f"HTTP {response.status_code}"
            
            return HealthCheckResult(
                name="api_endpoint",
                status=status,
                severity=severity,
                message=message,
                details={
                    "url": url,
                    "status_code": response.status_code,
                    "latency_ms": round(latency_ms, 1),
                },
                duration_ms=latency_ms,
                checked_at=datetime.now(timezone.utc),
            )
        
        except requests.Timeout:
            return HealthCheckResult(
                name="api_endpoint",
                status=HealthStatus.UNHEALTHY,
                severity=CheckSeverity.CRITICAL,
                message=f"Timeout after {timeout}s",
                details={"url": url, "timeout": timeout},
                duration_ms=(time.time() - start) * 1000,
                checked_at=datetime.now(timezone.utc),
            )
        except requests.RequestException as e:
            return HealthCheckResult(
                name="api_endpoint",
                status=HealthStatus.UNHEALTHY,
                severity=CheckSeverity.CRITICAL,
                message=f"Connection error: {str(e)}",
                details={"url": url, "error": str(e)},
                duration_ms=(time.time() - start) * 1000,
                checked_at=datetime.now(timezone.utc),
            )
    
    def check_drawdown(self) -> HealthCheckResult:
        """Check current drawdown levels."""
        start = time.time()
        collector = get_metrics_collector()
        safety = collector.get_safety_dashboard()
        dd = safety.drawdown
        
        # Drawdown thresholds
        DD_WARNING = 10.0
        DD_CRITICAL = 20.0
        
        if dd.current_drawdown_percent >= DD_CRITICAL:
            status = HealthStatus.UNHEALTHY
            severity = CheckSeverity.CRITICAL
            message = f"Critical drawdown: {dd.current_drawdown_percent:.1f}%"
        elif dd.current_drawdown_percent >= DD_WARNING:
            status = HealthStatus.DEGRADED
            severity = CheckSeverity.WARNING
            message = f"Elevated drawdown: {dd.current_drawdown_percent:.1f}%"
        else:
            status = HealthStatus.HEALTHY
            severity = CheckSeverity.INFO
            message = f"Drawdown OK: {dd.current_drawdown_percent:.1f}%"
        
        return HealthCheckResult(
            name="drawdown",
            status=status,
            severity=severity,
            message=message,
            details={
                "current_drawdown_percent": round(dd.current_drawdown_percent, 2),
                "max_drawdown_percent": round(dd.max_drawdown_percent, 2),
                "peak_equity": dd.peak_equity,
                "current_equity": dd.current_equity,
                "days_in_drawdown": dd.days_in_drawdown,
            },
            duration_ms=(time.time() - start) * 1000,
            checked_at=datetime.now(timezone.utc),
        )
    
    # -------------------------------------------------------------------------
    # COMPREHENSIVE HEALTH REPORT
    # -------------------------------------------------------------------------
    
    def run_all_checks(self, api_url: Optional[str] = None) -> SystemHealthReport:
        """Run all health checks and generate comprehensive report."""
        start = time.time()
        checks: List[HealthCheckResult] = []

        with self._check_lock:
            # Storage checks
            storage_files = [
                "positions.json",
                "trade_history.json",
                "analytics.json",
                "stats.json",
                "signals.json",
            ]
            for filename in storage_files:
                result = self.check_storage_file(filename)
                checks.append(result)
                self._trigger_alerts(result)

            # MTB storage checks
            try:
                from bots.mtb_bot.config import POSITIONS_FILE as MTB_POS, \
                    TRADES_FILE as MTB_TRD, STATS_FILE as MTB_STA
                for path, label in [(MTB_POS, "mtb_positions.json"),
                                    (MTB_TRD, "mtb_trades.json"),
                                    (MTB_STA, "mtb_stats.json")]:
                    result = self.check_storage_file(label, path)
                    checks.append(result)
                    self._trigger_alerts(result)
            except Exception:
                checks.append(HealthCheckResult(
                    name="mtb_config",
                    status=HealthStatus.DEGRADED,
                    severity=CheckSeverity.WARNING,
                    message="MTB config unavailable",
                    checked_at=datetime.now(timezone.utc),
                ))

            # PMB storage checks
            try:
                from bots.pmb_bot.config import POSITIONS_FILE as PMB_POS, \
                    TRADES_FILE as PMB_TRD, STATS_FILE as PMB_STA
                for path, label in [(PMB_POS, "pmb_positions.json"),
                                    (PMB_TRD, "pmb_trades.json"),
                                    (PMB_STA, "pmb_stats.json")]:
                    result = self.check_storage_file(label, path)
                    checks.append(result)
                    self._trigger_alerts(result)
            except Exception:
                checks.append(HealthCheckResult(
                    name="pmb_config",
                    status=HealthStatus.DEGRADED,
                    severity=CheckSeverity.WARNING,
                    message="PMB config unavailable",
                    checked_at=datetime.now(timezone.utc),
                ))
            
            # Safety checks
            checks.append(self.check_circuit_breaker())
            checks.append(self.check_trading_status())
            checks.append(self.check_drawdown())
            
            # System checks
            checks.append(self.check_system_resources())
            
            # Security checks
            checks.append(self.check_security())
            
            # API check (if URL provided)
            if api_url:
                checks.append(self.check_api_endpoint(api_url))
        
        # Trigger alerts for any unhealthy checks
        for check in checks:
            self._trigger_alerts(check)
        
        # Calculate summary
        summary = {
            "healthy": sum(1 for c in checks if c.status == HealthStatus.HEALTHY),
            "degraded": sum(1 for c in checks if c.status == HealthStatus.DEGRADED),
            "unhealthy": sum(1 for c in checks if c.status == HealthStatus.UNHEALTHY),
            "unknown": sum(1 for c in checks if c.status == HealthStatus.UNKNOWN),
            "total": len(checks),
        }
        
        # Collect issues
        critical_issues = [
            f"{c.name}: {c.message}" 
            for c in checks 
            if c.severity == CheckSeverity.CRITICAL and c.status != HealthStatus.HEALTHY
        ]
        warnings = [
            f"{c.name}: {c.message}" 
            for c in checks 
            if c.severity == CheckSeverity.WARNING and c.status != HealthStatus.HEALTHY
        ]
        
        # Determine overall status
        if summary["unhealthy"] > 0:
            overall_status = HealthStatus.UNHEALTHY
        elif summary["degraded"] > 0:
            overall_status = HealthStatus.DEGRADED
        elif summary["unknown"] > 0:
            overall_status = HealthStatus.UNKNOWN
        else:
            overall_status = HealthStatus.HEALTHY
        
        report = SystemHealthReport(
            overall_status=overall_status,
            checks=checks,
            summary=summary,
            critical_issues=critical_issues,
            warnings=warnings,
            generated_at=datetime.now(timezone.utc),
            duration_ms=(time.time() - start) * 1000,
        )
        
        # Store in history
        with self._check_lock:
            self._last_report = report
            self._check_history.append(report)
            if len(self._check_history) > self._MAX_HISTORY:
                self._check_history = self._check_history[-self._MAX_HISTORY:]
        
        return report
    
    def get_last_report(self) -> Optional[SystemHealthReport]:
        """Get the most recent health report."""
        with self._check_lock:
            return self._last_report
    
    def get_check_history(self, limit: int = 10) -> List[SystemHealthReport]:
        """Get recent health check history."""
        with self._check_lock:
            return list(reversed(self._check_history[-limit:]))
    
    # -------------------------------------------------------------------------
    # QUICK CHECKS
    # -------------------------------------------------------------------------
    
    def quick_check(self) -> Tuple[HealthStatus, str]:
        """Perform a quick health check returning status and message."""
        report = self.run_all_checks()
        
        if report.overall_status == HealthStatus.HEALTHY:
            return HealthStatus.HEALTHY, "All systems operational"
        elif report.overall_status == HealthStatus.DEGRADED:
            return HealthStatus.DEGRADED, f"Degraded: {', '.join(report.warnings[:3])}"
        elif report.overall_status == HealthStatus.UNHEALTHY:
            return HealthStatus.UNHEALTHY, f"Critical: {', '.join(report.critical_issues[:3])}"
        else:
            return HealthStatus.UNKNOWN, "Unable to determine health status"
    
    def is_healthy(self) -> bool:
        """Quick check if system is healthy."""
        status, _ = self.quick_check()
        return status == HealthStatus.HEALTHY


# =============================================================================
# SINGLETON ACCESSOR
# =============================================================================

_checker_instance: Optional[HealthChecker] = None
_checker_lock = threading.Lock()


def get_health_checker() -> HealthChecker:
    """Get the singleton HealthChecker instance."""
    global _checker_instance
    if _checker_instance is None:
        with _checker_lock:
            if _checker_instance is None:
                _checker_instance = HealthChecker()
    return _checker_instance
