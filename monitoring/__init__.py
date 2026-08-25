"""
PROJECT ALPHA - Production Monitoring and Observability Layer
============================================================

A comprehensive monitoring system for real-time visibility into:
- Trading safety status and circuit breakers
- Storage health and integrity
- Security events and access control
- Trading statistics and performance
- System resource utilization
- Automated Telegram alerts

Thread-safe implementation with graceful error handling.
"""

from .metrics_collector import MetricsCollector, get_metrics_collector
from .health_check import HealthChecker, get_health_checker
from .monitoring_dashboard import MonitoringDashboard
from .monitoring_api import create_monitoring_router
from .telegram_alerts import AlertManager, get_alert_manager, register_alert_callbacks

__all__ = [
    "MetricsCollector",
    "get_metrics_collector",
    "HealthChecker", 
    "get_health_checker",
    "MonitoringDashboard",
    "create_monitoring_router",
    "AlertManager",
    "get_alert_manager",
    "register_alert_callbacks",
]

__version__ = "1.0.0"
