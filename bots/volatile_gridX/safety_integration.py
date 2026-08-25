"""
PROJECT-ALPHA Production Safety Integration
Central module that integrates all safety components.

This module:
1. Initializes all safety systems
2. Provides unified health check
3. Coordinates emergency responses
4. Exposes safety status API
"""

import logging
import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, Optional

# Import all safety modules
from .thread_safety import (
    get_lock_status,
    position_lock,
    storage_lock
)
from .circuit_breaker import (
    get_circuit_breaker,
    check_can_trade,
    get_breaker_status,
    TradingState
)
from .safe_storage import (
    check_storage_integrity,
    create_backup,
    list_backups,
    get_storage_stats
)
from .telegram_security import (
    get_security_status,
    get_security_logger,
    SECURITY_ENABLED
)
from .market_analysis import analyze_coin
from .risk_engine import get_risk_status

logger = logging.getLogger("vgx.safety_integration")


# ============================================================
# INITIALIZATION
# ============================================================

_initialized = False


def initialize_safety_systems() -> Dict[str, bool]:
    """Initialize all safety systems and return status."""
    global _initialized
    
    results = {
        "thread_safety": False,
        "circuit_breaker": False,
        "safe_storage": False,
        "telegram_security": False,
        "market_analysis": False
    }
    
    try:
        # Thread safety (already initialized on import)
        _ = get_lock_status()
        results["thread_safety"] = True
        logger.info("Thread safety: OK")
    except Exception as e:
        logger.error("Thread safety init failed: %s", e)
    
    try:
        # Circuit breaker
        cb = get_circuit_breaker()
        results["circuit_breaker"] = cb is not None
        logger.info("Circuit breaker: OK (state: %s)", cb.state.trading_state)
    except Exception as e:
        logger.error("Circuit breaker init failed: %s", e)
    
    try:
        # Safe storage
        integrity = check_storage_integrity()
        results["safe_storage"] = integrity["overall_status"] == "healthy"
        logger.info("Safe storage: %s", integrity["overall_status"])
    except Exception as e:
        logger.error("Safe storage init failed: %s", e)
    
    try:
        # Telegram security
        results["telegram_security"] = True
        sec_status = get_security_status()
        logger.info("Telegram security: %s (admins: %d)", 
                   "enabled" if SECURITY_ENABLED else "disabled",
                   sec_status["admin_count"])
    except Exception as e:
        logger.error("Telegram security init failed: %s", e)
    
    try:
        # Market analysis (test with dummy data)
        test_result = analyze_coin("TEST", [{"price": 100}, {"price": 101}])
        results["market_analysis"] = test_result is not None
        logger.info("Market analysis: OK")
    except Exception as e:
        logger.error("Market analysis init failed: %s", e)
    
    _initialized = all(results.values())
    
    if _initialized:
        logger.info("All safety systems initialized successfully")
    else:
        failed = [k for k, v in results.items() if not v]
        logger.warning("Safety systems partially initialized. Failed: %s", failed)
    
    return results


# ============================================================
# HEALTH CHECK
# ============================================================

def get_safety_health() -> Dict[str, Any]:
    """
    Get comprehensive health status of all safety systems.
    """
    health = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "overall_status": "healthy",
        "components": {}
    }
    
    issues = []
    
    # Thread safety
    try:
        lock_status = get_lock_status()
        pending = len(lock_status.get("pending_orders", []))
        health["components"]["thread_safety"] = {
            "status": "healthy",
            "pending_orders": pending
        }
        if pending > 5:
            issues.append("High pending order count")
    except Exception as e:
        health["components"]["thread_safety"] = {"status": "error", "error": str(e)}
        issues.append("Thread safety error")
    
    # Circuit breaker
    try:
        breaker = get_breaker_status()
        can_trade = breaker.get("can_trade", False)
        state = breaker.get("trading_state", "UNKNOWN")
        
        health["components"]["circuit_breaker"] = {
            "status": "healthy" if state == "ACTIVE" else "triggered",
            "trading_state": state,
            "can_trade": can_trade,
            "daily_pnl_pct": breaker.get("daily_pnl_pct", 0),
            "drawdown_pct": breaker.get("drawdown_pct", 0)
        }
        
        if not can_trade:
            issues.append(f"Circuit breaker: {state}")
    except Exception as e:
        health["components"]["circuit_breaker"] = {"status": "error", "error": str(e)}
        issues.append("Circuit breaker error")
    
    # Storage
    try:
        integrity = check_storage_integrity()
        stats = get_storage_stats()
        
        health["components"]["storage"] = {
            "status": integrity["overall_status"],
            "files": integrity["files"],
            "positions_count": stats.positions_count,
            "total_size_kb": stats.total_file_size_kb
        }
        
        if integrity["overall_status"] != "healthy":
            issues.append(f"Storage: {integrity['overall_status']}")
    except Exception as e:
        health["components"]["storage"] = {"status": "error", "error": str(e)}
        issues.append("Storage error")
    
    # Telegram security
    try:
        sec = get_security_status()
        health["components"]["telegram_security"] = {
            "status": "healthy",
            "enabled": sec["enabled"],
            "denied_24h": sec["denied_24h"]
        }
        
        if sec["denied_24h"] > 10:
            issues.append(f"High denied access attempts: {sec['denied_24h']}")
    except Exception as e:
        health["components"]["telegram_security"] = {"status": "error", "error": str(e)}
        issues.append("Telegram security error")
    
    # Risk engine
    try:
        risk = get_risk_status()
        health["components"]["risk_engine"] = {
            "status": "healthy" if risk["trading_allowed"] else "restricted",
            "market_regime": risk["market_regime"],
            "cooldown_active": risk["cooldown"]["active"],
            "position_utilization": risk["positions"]["utilization_pct"]
        }
        
        if risk["cooldown"]["active"]:
            issues.append("Cooldown active")
        if risk["market_regime"] in ("BEAR", "HIGH_VOL"):
            issues.append(f"Unfavorable market: {risk['market_regime']}")
    except Exception as e:
        health["components"]["risk_engine"] = {"status": "error", "error": str(e)}
        issues.append("Risk engine error")
    
    # Determine overall status
    if issues:
        health["overall_status"] = "degraded" if len(issues) < 3 else "critical"
        health["issues"] = issues
    
    return health


# ============================================================
# EMERGENCY RESPONSE
# ============================================================

async def trigger_emergency_stop(reason: str) -> Dict[str, Any]:
    """
    Trigger emergency stop across all systems.
    """
    logger.critical("EMERGENCY STOP TRIGGERED: %s", reason)
    
    results = {
        "triggered_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "actions": []
    }
    
    # Create emergency backup
    try:
        backup_name = create_backup("emergency")
        results["actions"].append(f"Backup created: {backup_name}")
    except Exception as e:
        results["actions"].append(f"Backup failed: {e}")
    
    # Set circuit breaker to emergency stop
    try:
        from .circuit_breaker import get_circuit_breaker, TradingState
        cb = get_circuit_breaker()
        cb.state.trading_state = TradingState.EMERGENCY_STOP.value
        cb._save_state()
        results["actions"].append("Circuit breaker set to EMERGENCY_STOP")
    except Exception as e:
        results["actions"].append(f"Circuit breaker update failed: {e}")
    
    # Close all positions
    try:
        from .trading_engine import emergency_close_all
        close_results = emergency_close_all(f"EMERGENCY: {reason}")
        results["actions"].append(f"Closed {close_results['closed']} positions")
        results["positions_closed"] = close_results
    except Exception as e:
        results["actions"].append(f"Position close failed: {e}")
    
    # Log security event
    try:
        sec_logger = get_security_logger()
        from .telegram_security import SecurityEvent
        sec_logger.log_event(SecurityEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            event_type="suspicious",
            user_id=0,
            username="SYSTEM",
            command="EMERGENCY_STOP",
            details=reason
        ))
        results["actions"].append("Security event logged")
    except Exception as e:
        results["actions"].append(f"Security log failed: {e}")
    
    logger.critical("EMERGENCY STOP COMPLETE: %s", results)
    return results


# ============================================================
# PRODUCTION READINESS CHECK
# ============================================================

def production_readiness_check() -> Dict[str, Any]:
    """
    Comprehensive check for production deployment readiness.
    """
    checks = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ready": True,
        "score": 100,
        "checks": {}
    }
    
    # 1. Thread safety initialized
    try:
        locks = get_lock_status()
        checks["checks"]["thread_safety"] = {
            "passed": True,
            "message": "Thread locks initialized"
        }
    except Exception:
        checks["checks"]["thread_safety"] = {
            "passed": False,
            "message": "Thread safety not initialized"
        }
        checks["score"] -= 20
    
    # 2. Circuit breaker functional
    try:
        cb = get_circuit_breaker()
        cb_ok = cb is not None and cb.state is not None
        checks["checks"]["circuit_breaker"] = {
            "passed": cb_ok,
            "message": f"State: {cb.state.trading_state}" if cb_ok else "Not initialized"
        }
        if not cb_ok:
            checks["score"] -= 20
    except Exception as e:
        checks["checks"]["circuit_breaker"] = {"passed": False, "message": str(e)}
        checks["score"] -= 20
    
    # 3. Storage integrity
    try:
        integrity = check_storage_integrity()
        storage_ok = integrity["overall_status"] == "healthy"
        checks["checks"]["storage"] = {
            "passed": storage_ok,
            "message": integrity["overall_status"]
        }
        if not storage_ok:
            checks["score"] -= 15
    except Exception as e:
        checks["checks"]["storage"] = {"passed": False, "message": str(e)}
        checks["score"] -= 15
    
    # 4. Telegram security configured
    try:
        sec = get_security_status()
        sec_ok = sec["enabled"] and sec["admin_count"] > 0
        checks["checks"]["telegram_security"] = {
            "passed": sec_ok,
            "message": f"Admins: {sec['admin_count']}, Allowed: {sec['allowed_count']}"
        }
        if not sec_ok:
            checks["score"] -= 15
    except Exception as e:
        checks["checks"]["telegram_security"] = {"passed": False, "message": str(e)}
        checks["score"] -= 15
    
    # 5. Risk engine functional
    try:
        risk = get_risk_status()
        risk_ok = "market_regime" in risk
        checks["checks"]["risk_engine"] = {
            "passed": risk_ok,
            "message": f"Regime: {risk.get('market_regime', 'N/A')}"
        }
        if not risk_ok:
            checks["score"] -= 15
    except Exception as e:
        checks["checks"]["risk_engine"] = {"passed": False, "message": str(e)}
        checks["score"] -= 15
    
    # 6. Market analysis functional
    try:
        result = analyze_coin("BTC", [{"price": 100, "volume": 1000}] * 10)
        analysis_ok = result is not None and hasattr(result, 'score')
        checks["checks"]["market_analysis"] = {
            "passed": analysis_ok,
            "message": "Analysis functional" if analysis_ok else "Analysis failed"
        }
        if not analysis_ok:
            checks["score"] -= 15
    except Exception as e:
        checks["checks"]["market_analysis"] = {"passed": False, "message": str(e)}
        checks["score"] -= 15
    
    # 7. Backups exist
    try:
        backups = list_backups()
        backup_ok = len(backups) > 0
        checks["checks"]["backups"] = {
            "passed": backup_ok,
            "message": f"{len(backups)} backups available"
        }
        if not backup_ok:
            checks["score"] -= 5
    except Exception as e:
        checks["checks"]["backups"] = {"passed": False, "message": str(e)}
        checks["score"] -= 5
    
    # Determine readiness
    failed_checks = [k for k, v in checks["checks"].items() if not v["passed"]]
    checks["ready"] = len(failed_checks) == 0
    checks["failed_checks"] = failed_checks
    
    return checks


# ============================================================
# COMBINED STATUS API
# ============================================================

def get_full_safety_status() -> Dict[str, Any]:
    """
    Get complete safety status for dashboard/API consumption.
    """
    return {
        "health": get_safety_health(),
        "production_readiness": production_readiness_check(),
        "circuit_breaker": get_breaker_status(),
        "storage": {
            "integrity": check_storage_integrity(),
            "stats": get_storage_stats().__dict__
        },
        "security": get_security_status(),
        "risk": get_risk_status(),
        "locks": get_lock_status()
    }
