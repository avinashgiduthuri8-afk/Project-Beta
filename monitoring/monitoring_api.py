"""
Monitoring API - FastAPI Endpoints for Observability
=====================================================

Provides REST API endpoints for the monitoring dashboard:
- /api/monitoring/dashboard - Full dashboard data
- /api/monitoring/health - Health check endpoints
- /api/monitoring/metrics - Raw metrics access
- /api/monitoring/alerts - Alert management

Thread-safe with rate limiting and authentication support.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import time
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from .metrics_collector import (
    get_metrics_collector,
    TradingStatus,
    CircuitBreakerStatus,
)
from .health_check import get_health_checker, HealthStatus
from .monitoring_dashboard import MonitoringDashboard

logger = logging.getLogger("monitoring.api")


# =============================================================================
# REQUEST/RESPONSE MODELS
# =============================================================================

class HealthResponse(BaseModel):
    """Health check response model."""
    status: str
    message: str
    timestamp: str
    checks: Optional[Dict[str, Any]] = None


class MetricsResponse(BaseModel):
    """Metrics response model."""
    safety: Dict[str, Any]
    trading: Dict[str, Any]
    system: Dict[str, Any]
    security: Dict[str, Any]
    storage: Dict[str, Any]
    timestamp: str


class AlertRequest(BaseModel):
    """Alert configuration request."""
    threshold_key: str
    threshold_value: float


class SecurityEventRequest(BaseModel):
    """Security event recording request."""
    event_type: str
    user_id: Optional[str] = None
    ip_address: Optional[str] = None
    details: Optional[str] = None
    success: bool = False


class TradingStatusRequest(BaseModel):
    """Trading status update request."""
    status: str = Field(..., description="ACTIVE, PAUSED, or EMERGENCY")


class PnLUpdateRequest(BaseModel):
    """PnL update request."""
    daily: float = 0.0
    weekly: float = 0.0
    monthly: float = 0.0
    total: float = 0.0
    starting_capital: float = 100000.0


class TradeRecordRequest(BaseModel):
    """Trade record request."""
    coin: str
    side: str
    quantity: float
    price: float
    pnl: float = 0.0
    fees: float = 0.0
    metadata: Optional[Dict[str, Any]] = None


# =============================================================================
# RATE LIMITING
# =============================================================================

class RateLimiter:
    """Simple in-memory rate limiter."""
    
    def __init__(self, max_requests: int = 60, window_seconds: int = 60):
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._requests: Dict[str, List[float]] = {}
        self._lock = threading.Lock()
    
    def is_allowed(self, client_id: str) -> bool:
        """Check if request is allowed for client."""
        now = time.time()
        with self._lock:
            if client_id not in self._requests:
                self._requests[client_id] = []
            
            # Clean old requests
            self._requests[client_id] = [
                t for t in self._requests[client_id]
                if now - t < self._window_seconds
            ]
            
            # Check limit
            if len(self._requests[client_id]) >= self._max_requests:
                return False
            
            self._requests[client_id].append(now)
            return True


# Global rate limiter
_rate_limiter = RateLimiter(max_requests=120, window_seconds=60)


def rate_limit(func: Callable) -> Callable:
    """Rate limiting decorator."""
    @functools.wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        client_ip = request.client.host if request.client else "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            # Record rate limit violation
            collector = get_metrics_collector()
            collector.record_security_event(
                event_type="rate_limit",
                ip_address=client_ip,
                details=f"Rate limit exceeded for {func.__name__}",
            )
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
        return await func(request, *args, **kwargs)
    return wrapper


# =============================================================================
# API ROUTER FACTORY
# =============================================================================

def create_monitoring_router(
    prefix: str = "/api/monitoring",
    require_auth: bool = False,
    auth_token: Optional[str] = None,
) -> APIRouter:
    """
    Create monitoring API router.
    
    Args:
        prefix: URL prefix for all routes
        require_auth: Whether to require authentication
        auth_token: Bearer token for authentication (if require_auth=True)
    
    Returns:
        Configured FastAPI router
    """
    router = APIRouter(prefix=prefix, tags=["monitoring"])
    
    # Initialize components
    collector = get_metrics_collector()
    health_checker = get_health_checker()
    dashboard = MonitoringDashboard(collector, health_checker)
    
    # Authentication middleware
    def check_auth(request: Request) -> None:
        if not require_auth:
            return
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing authentication")
        token = auth_header.replace("Bearer ", "")
        if token != auth_token:
            collector.record_security_event(
                event_type="login_attempt",
                ip_address=request.client.host if request.client else None,
                details="Invalid monitoring API token",
                success=False,
            )
            raise HTTPException(status_code=403, detail="Invalid token")
    
    # -------------------------------------------------------------------------
    # DASHBOARD ENDPOINTS
    # -------------------------------------------------------------------------
    
    @router.get("/dashboard", response_class=JSONResponse)
    async def get_dashboard(request: Request) -> Dict[str, Any]:
        """Get complete monitoring dashboard data."""
        check_auth(request)
        try:
            return dashboard.get_dashboard(use_cache=True)
        except Exception as e:
            logger.error("Dashboard error: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.get("/dashboard/summary", response_class=JSONResponse)
    async def get_dashboard_summary(request: Request) -> Dict[str, Any]:
        """Get lightweight dashboard summary."""
        check_auth(request)
        try:
            return dashboard.get_dashboard_summary()
        except Exception as e:
            logger.error("Dashboard summary error: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.get("/dashboard/html", response_class=HTMLResponse)
    async def get_dashboard_html(request: Request) -> str:
        """Get dashboard as rendered HTML."""
        check_auth(request)
        try:
            return dashboard.render_html()
        except Exception as e:
            logger.error("Dashboard HTML error: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    # -------------------------------------------------------------------------
    # HEALTH ENDPOINTS
    # -------------------------------------------------------------------------
    
    @router.get("/health", response_model=HealthResponse)
    async def health_check(request: Request) -> HealthResponse:
        """Quick health check endpoint."""
        try:
            status, message = health_checker.quick_check()
            return HealthResponse(
                status=status.value,
                message=message,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        except Exception as e:
            logger.error("Health check error: %s", e, exc_info=True)
            return HealthResponse(
                status="ERROR",
                message=str(e),
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
    
    @router.get("/health/detailed", response_class=JSONResponse)
    async def detailed_health_check(
        request: Request,
        api_url: Optional[str] = Query(None, description="Optional API URL to check"),
    ) -> Dict[str, Any]:
        """Detailed health check with all components."""
        check_auth(request)
        try:
            report = health_checker.run_all_checks(api_url=api_url)
            return report.to_dict()
        except Exception as e:
            logger.error("Detailed health check error: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.get("/health/history", response_class=JSONResponse)
    async def health_history(
        request: Request,
        limit: int = Query(10, ge=1, le=100),
    ) -> List[Dict[str, Any]]:
        """Get health check history."""
        check_auth(request)
        try:
            history = health_checker.get_check_history(limit=limit)
            return [h.to_dict() for h in history]
        except Exception as e:
            logger.error("Health history error: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    # -------------------------------------------------------------------------
    # METRICS ENDPOINTS
    # -------------------------------------------------------------------------
    
    @router.get("/metrics", response_class=JSONResponse)
    async def get_metrics(request: Request) -> Dict[str, Any]:
        """Get all current metrics."""
        check_auth(request)
        try:
            return collector.collect_all()
        except Exception as e:
            logger.error("Metrics error: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.get("/metrics/safety", response_class=JSONResponse)
    async def get_safety_metrics(request: Request) -> Dict[str, Any]:
        """Get safety dashboard metrics."""
        check_auth(request)
        try:
            safety = collector.get_safety_dashboard()
            return {
                "trading_status": safety.trading_status.value,
                "circuit_breaker_status": safety.circuit_breaker_status.value,
                "pnl": {
                    "daily": safety.pnl.daily_pnl,
                    "daily_percent": safety.pnl.daily_pnl_percent,
                    "weekly": safety.pnl.weekly_pnl,
                    "weekly_percent": safety.pnl.weekly_pnl_percent,
                    "monthly": safety.pnl.monthly_pnl,
                    "monthly_percent": safety.pnl.monthly_pnl_percent,
                },
                "drawdown": {
                    "current_percent": safety.drawdown.current_drawdown_percent,
                    "max_percent": safety.drawdown.max_drawdown_percent,
                    "peak_equity": safety.drawdown.peak_equity,
                    "current_equity": safety.drawdown.current_equity,
                },
                "kill_switch_active": safety.kill_switch_active,
                "emergency_stop_active": safety.emergency_stop_active,
                "last_updated": safety.last_updated.isoformat() if safety.last_updated else None,
            }
        except Exception as e:
            logger.error("Safety metrics error: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.get("/metrics/trading", response_class=JSONResponse)
    async def get_trading_metrics(request: Request) -> Dict[str, Any]:
        """Get trading statistics."""
        check_auth(request)
        try:
            stats = collector.get_trading_stats()
            return {
                "total_trades": stats.total_trades,
                "winning_trades": stats.winning_trades,
                "losing_trades": stats.losing_trades,
                "win_rate": stats.win_rate,
                "loss_rate": stats.loss_rate,
                "profit_factor": stats.profit_factor,
                "average_win": stats.average_win,
                "average_loss": stats.average_loss,
                "largest_win": stats.largest_win,
                "largest_loss": stats.largest_loss,
                "max_drawdown": stats.max_drawdown,
                "open_positions": stats.open_positions,
                "total_volume": stats.total_volume,
                "last_trade_time": stats.last_trade_time.isoformat() if stats.last_trade_time else None,
            }
        except Exception as e:
            logger.error("Trading metrics error: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.get("/metrics/system", response_class=JSONResponse)
    async def get_system_metrics(request: Request) -> Dict[str, Any]:
        """Get system resource metrics."""
        check_auth(request)
        try:
            metrics = collector.collect_system_metrics()
            return {
                "cpu_percent": metrics.cpu_percent,
                "cpu_count": metrics.cpu_count,
                "memory_percent": metrics.memory_percent,
                "memory_used_mb": metrics.memory_used_mb,
                "memory_total_mb": metrics.memory_total_mb,
                "disk_percent": metrics.disk_percent,
                "disk_used_gb": metrics.disk_used_gb,
                "disk_total_gb": metrics.disk_total_gb,
                "uptime_seconds": metrics.uptime_seconds,
                "uptime_formatted": metrics.uptime_formatted,
                "thread_count": metrics.thread_count,
                "open_files": metrics.open_files,
                "network_connections": metrics.network_connections,
                "process_id": metrics.process_id,
                "api_latency_ms": metrics.api_latency_ms,
                "last_measured": metrics.last_measured.isoformat() if metrics.last_measured else None,
            }
        except Exception as e:
            logger.error("System metrics error: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.get("/metrics/security", response_class=JSONResponse)
    async def get_security_metrics(request: Request) -> Dict[str, Any]:
        """Get security metrics."""
        check_auth(request)
        try:
            security = collector.get_security_metrics()
            return {
                "authorized_users_count": len(security.authorized_users),
                "authorized_users": security.authorized_users[:20],  # Limit exposure
                "failed_login_attempts": security.failed_login_attempts,
                "failed_login_last_hour": security.failed_login_last_hour,
                "rate_limit_violations": security.rate_limit_violations,
                "rate_limit_violations_last_hour": security.rate_limit_violations_last_hour,
                "blocked_ips_count": len(security.blocked_ips),
                "security_events_today": security.security_events_today,
                "last_security_event": security.last_security_event.isoformat() if security.last_security_event else None,
            }
        except Exception as e:
            logger.error("Security metrics error: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.get("/metrics/storage", response_class=JSONResponse)
    async def get_storage_metrics(request: Request) -> Dict[str, Any]:
        """Get storage health metrics."""
        check_auth(request)
        try:
            storage = collector.collect_storage_health()
            return {
                filename: {
                    "status": m.status.value,
                    "size_bytes": m.size_bytes,
                    "checksum": m.checksum,
                    "record_count": m.record_count,
                    "is_valid_json": m.is_valid_json,
                    "backup_available": m.backup_available,
                    "last_modified": m.last_modified.isoformat() if m.last_modified else None,
                    "error_message": m.error_message,
                }
                for filename, m in storage.items()
            }
        except Exception as e:
            logger.error("Storage metrics error: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    # -------------------------------------------------------------------------
    # UPDATE ENDPOINTS
    # -------------------------------------------------------------------------
    
    @router.post("/update/trading-status", response_class=JSONResponse)
    async def update_trading_status(
        request: Request,
        body: TradingStatusRequest,
    ) -> Dict[str, Any]:
        """Update trading status."""
        check_auth(request)
        try:
            status_map = {
                "ACTIVE": TradingStatus.ACTIVE,
                "PAUSED": TradingStatus.PAUSED,
                "EMERGENCY": TradingStatus.EMERGENCY,
            }
            if body.status.upper() not in status_map:
                raise HTTPException(status_code=400, detail=f"Invalid status: {body.status}")
            
            collector.update_trading_status(status_map[body.status.upper()])
            return {"success": True, "status": body.status.upper()}
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Update trading status error: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/update/pnl", response_class=JSONResponse)
    async def update_pnl(
        request: Request,
        body: PnLUpdateRequest,
    ) -> Dict[str, Any]:
        """Update PnL metrics."""
        check_auth(request)
        try:
            collector.update_pnl(
                daily=body.daily,
                weekly=body.weekly,
                monthly=body.monthly,
                total=body.total,
                starting_capital=body.starting_capital,
            )
            return {"success": True}
        except Exception as e:
            logger.error("Update PnL error: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/update/circuit-breaker", response_class=JSONResponse)
    async def update_circuit_breaker(
        request: Request,
        status: str = Query(..., description="CLOSED, OPEN, or HALF_OPEN"),
    ) -> Dict[str, Any]:
        """Update circuit breaker status."""
        check_auth(request)
        try:
            status_map = {
                "CLOSED": CircuitBreakerStatus.CLOSED,
                "OPEN": CircuitBreakerStatus.OPEN,
                "HALF_OPEN": CircuitBreakerStatus.HALF_OPEN,
            }
            if status.upper() not in status_map:
                raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
            
            collector.update_circuit_breaker(status_map[status.upper()])
            return {"success": True, "status": status.upper()}
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Update circuit breaker error: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/update/emergency-stop", response_class=JSONResponse)
    async def update_emergency_stop(
        request: Request,
        active: bool = Query(...),
    ) -> Dict[str, Any]:
        """Activate or deactivate emergency stop."""
        check_auth(request)
        try:
            collector.set_emergency_stop(active)
            return {"success": True, "emergency_stop_active": active}
        except Exception as e:
            logger.error("Update emergency stop error: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/record/trade", response_class=JSONResponse)
    async def record_trade(
        request: Request,
        body: TradeRecordRequest,
    ) -> Dict[str, Any]:
        """Record a trade event."""
        check_auth(request)
        try:
            trade_data = {
                "coin": body.coin,
                "side": body.side,
                "quantity": body.quantity,
                "price": body.price,
                "pnl": body.pnl,
                "fees": body.fees,
                "metadata": body.metadata or {},
            }
            collector.record_trade(trade_data)
            return {"success": True}
        except Exception as e:
            logger.error("Record trade error: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/record/security-event", response_class=JSONResponse)
    async def record_security_event(
        request: Request,
        body: SecurityEventRequest,
    ) -> Dict[str, Any]:
        """Record a security event."""
        check_auth(request)
        try:
            collector.record_security_event(
                event_type=body.event_type,
                user_id=body.user_id,
                ip_address=body.ip_address,
                details=body.details,
                success=body.success,
            )
            return {"success": True}
        except Exception as e:
            logger.error("Record security event error: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    # -------------------------------------------------------------------------
    # ALERT CONFIGURATION
    # -------------------------------------------------------------------------
    
    @router.get("/alerts/thresholds", response_class=JSONResponse)
    async def get_alert_thresholds(request: Request) -> Dict[str, Any]:
        """Get current alert thresholds."""
        check_auth(request)
        try:
            return health_checker.get_thresholds()
        except Exception as e:
            logger.error("Get thresholds error: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/alerts/thresholds", response_class=JSONResponse)
    async def set_alert_threshold(
        request: Request,
        body: AlertRequest,
    ) -> Dict[str, Any]:
        """Set an alert threshold."""
        check_auth(request)
        try:
            health_checker.set_threshold(body.threshold_key, body.threshold_value)
            return {
                "success": True,
                "threshold_key": body.threshold_key,
                "threshold_value": body.threshold_value,
            }
        except Exception as e:
            logger.error("Set threshold error: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    # -------------------------------------------------------------------------
    # EVENTS
    # -------------------------------------------------------------------------
    
    @router.get("/events/security", response_class=JSONResponse)
    async def get_security_events(
        request: Request,
        limit: int = Query(50, ge=1, le=500),
    ) -> List[Dict[str, Any]]:
        """Get recent security events."""
        check_auth(request)
        try:
            return collector.get_security_events(limit=limit)
        except Exception as e:
            logger.error("Get security events error: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.get("/events/trades", response_class=JSONResponse)
    async def get_trade_events(
        request: Request,
        limit: int = Query(50, ge=1, le=500),
    ) -> List[Dict[str, Any]]:
        """Get recent trade events."""
        check_auth(request)
        try:
            return collector.get_recent_trades(limit=limit)
        except Exception as e:
            logger.error("Get trade events error: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    return router


# =============================================================================
# STANDALONE APP (for testing)
# =============================================================================

def create_standalone_app():
    """Create a standalone FastAPI app with monitoring routes."""
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    
    app = FastAPI(
        title="PROJECT ALPHA Monitoring API",
        description="Production monitoring and observability endpoints",
        version="1.0.0",
    )
    
    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Include monitoring router
    router = create_monitoring_router()
    app.include_router(router)
    
    @app.get("/")
    async def root():
        return {"service": "PROJECT ALPHA Monitoring", "status": "running"}
    
    return app


if __name__ == "__main__":
    import uvicorn
    app = create_standalone_app()
    uvicorn.run(app, host="0.0.0.0", port=8002)
