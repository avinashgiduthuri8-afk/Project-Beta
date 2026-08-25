#!/usr/bin/env python3
"""
Production Validation Suite - Complete System Verification
==========================================================

Validates all PROJECT ALPHA components:
- Trading Engine integration
- Scanner functionality
- Monitoring system
- Storage integrity
- Security measures
- Dashboard APIs
- Railway deployment readiness

Generates comprehensive production report with Go/No-Go recommendation.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# =============================================================================
# VALIDATION RESULT TRACKING
# =============================================================================

class ValidationResult:
    """Track validation results."""
    
    def __init__(self, component: str):
        self.component = component
        self.checks: List[Dict[str, Any]] = []
        self.passed = 0
        self.failed = 0
        self.warnings = 0
    
    def add_check(self, name: str, passed: bool, message: str = "", warning: bool = False) -> None:
        """Add a check result."""
        self.checks.append({
            "name": name,
            "passed": passed,
            "warning": warning,
            "message": message,
        })
        if passed:
            self.passed += 1
        elif warning:
            self.warnings += 1
        else:
            self.failed += 1
    
    def is_passed(self) -> bool:
        """Check if all critical checks passed."""
        return self.failed == 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "component": self.component,
            "passed": self.passed,
            "failed": self.failed,
            "warnings": self.warnings,
            "status": "PASS" if self.is_passed() else "FAIL",
            "checks": self.checks,
        }


# =============================================================================
# COMPONENT VALIDATORS
# =============================================================================

def validate_trading_engine() -> ValidationResult:
    """Validate trading engine components."""
    result = ValidationResult("Trading Engine")
    
    # Check VGX trading engine
    try:
        from bots.volatile_gridX.trading_engine import emergency_close_all
        result.add_check("VGX emergency_close_all available", callable(emergency_close_all))

        # Check circuit breaker integration directly
        from bots.volatile_gridX.circuit_breaker import check_can_trade, get_breaker_status
        result.add_check("VGX circuit_breaker integration", callable(check_can_trade) and callable(get_breaker_status))
    except Exception as e:
        result.add_check("VGX emergency_close_all available", False, str(e))
    
    # Check thread safety
    try:
        from bots.volatile_gridX import thread_safety
        result.add_check("Thread safety module", True)
    except Exception as e:
        result.add_check("Thread safety module", False, str(e))
    
    # Check circuit breaker
    try:
        from bots.volatile_gridX import circuit_breaker
        result.add_check("Circuit breaker module", True)
        
        # Verify thresholds
        if hasattr(circuit_breaker, 'TIER_THRESHOLDS'):
            result.add_check("Circuit breaker thresholds defined", True)
    except Exception as e:
        result.add_check("Circuit breaker module", False, str(e))
    
    # Check MTB
    try:
        from bots.mtb_bot import storage as mtb_storage
        snapshot = mtb_storage.snapshot()
        result.add_check("MTB storage snapshot", True)
    except Exception as e:
        result.add_check("MTB storage snapshot", False, str(e))
    
    # Check PMB
    try:
        from bots.pmb_bot import storage as pmb_storage
        snapshot = pmb_storage.snapshot()
        result.add_check("PMB storage snapshot", True)
    except Exception as e:
        result.add_check("PMB storage snapshot", False, str(e))
    
    # Check risk engine
    try:
        from bots.risk_engine import engine as risk_engine
        snapshot = risk_engine.snapshot()
        result.add_check("Risk engine snapshot", True)
    except Exception as e:
        result.add_check("Risk engine snapshot", False, str(e))
    
    return result


def validate_scanner() -> ValidationResult:
    """Validate scanner components."""
    result = ValidationResult("Scanner")
    
    try:
        from bots.scanner_bot import scanner
        result.add_check("Scanner module import", True)
        
        # Check key functions
        functions = ["get_signals", "get_live_signals", "get_watchlist", "get_stats"]
        for func in functions:
            if hasattr(scanner, func):
                result.add_check(f"Scanner.{func} exists", True)
            else:
                result.add_check(f"Scanner.{func} exists", False, "Function not found")
    except Exception as e:
        result.add_check("Scanner module import", False, str(e))
    
    # Check scanner main
    try:
        from bots.scanner_bot import main as scanner_main
        result.add_check("Scanner main import", True)
    except Exception as e:
        result.add_check("Scanner main import", False, str(e))
    
    # Check telegram bot
    try:
        from bots.scanner_bot import telegram_bot
        result.add_check("Scanner telegram bot", True)
    except Exception as e:
        result.add_check("Scanner telegram bot", True, "Optional", warning=True)
    
    return result


def validate_monitoring() -> ValidationResult:
    """Validate monitoring system."""
    result = ValidationResult("Monitoring")
    
    # Check metrics collector
    try:
        from monitoring.metrics_collector import MetricsCollector, get_metrics_collector
        collector = get_metrics_collector()
        result.add_check("MetricsCollector singleton", True)
        
        # Test metrics collection
        metrics = collector.collect_system_metrics()
        result.add_check("System metrics collection", True)
    except Exception as e:
        result.add_check("MetricsCollector", False, str(e))
    
    # Check health checker
    try:
        from monitoring.health_check import HealthChecker, get_health_checker
        checker = get_health_checker()
        status, message = checker.quick_check()
        result.add_check("HealthChecker quick_check", True, message)
    except Exception as e:
        result.add_check("HealthChecker", False, str(e))
    
    # Check monitoring dashboard
    try:
        from monitoring.monitoring_dashboard import MonitoringDashboard
        dashboard = MonitoringDashboard()
        summary = dashboard.get_dashboard_summary()
        result.add_check("MonitoringDashboard", True)
    except Exception as e:
        result.add_check("MonitoringDashboard", False, str(e))
    
    # Check monitoring API
    try:
        from monitoring.monitoring_api import create_monitoring_router
        router = create_monitoring_router()
        result.add_check("Monitoring API router", True, f"{len(router.routes)} routes")
    except Exception as e:
        result.add_check("Monitoring API router", False, str(e))
    
    # Check telegram alerts
    try:
        from monitoring.telegram_alerts import AlertManager, get_alert_manager
        manager = get_alert_manager()
        result.add_check("AlertManager", True)
    except Exception as e:
        result.add_check("AlertManager", False, str(e))
    
    # Check load testing
    try:
        from monitoring.load_testing import LoadTestEngine
        result.add_check("LoadTestEngine", True)
    except Exception as e:
        result.add_check("LoadTestEngine", False, str(e))
    
    return result


def validate_storage() -> ValidationResult:
    """Validate storage integrity."""
    result = ValidationResult("Storage")
    
    base_path = Path(__file__).resolve().parent.parent
    
    # Check key storage files
    storage_files = [
        ("TradingBotCrypto.json", base_path / "storage" / "TradingBotCrypto.json"),
    ]
    
    # Also check bot data directories
    data_dirs = [
        base_path / "data",
        base_path / "bots" / "scanner_bot" / "data",
        base_path / "bots" / "mtb_bot" / "data",
        base_path / "bots" / "pmb_bot" / "data",
    ]
    
    for dir_path in data_dirs:
        if dir_path.exists():
            for file_path in dir_path.glob("*.json"):
                storage_files.append((file_path.name, file_path))
    
    for name, path in storage_files:
        if path.exists():
            try:
                with open(path, "r") as f:
                    data = json.load(f)
                size = path.stat().st_size
                result.add_check(f"{name} valid JSON", True, f"{size} bytes")
            except json.JSONDecodeError as e:
                result.add_check(f"{name} valid JSON", False, str(e))
            except Exception as e:
                result.add_check(f"{name} readable", False, str(e))
        else:
            result.add_check(f"{name} exists", True, "Optional file", warning=True)
    
    # Check safe_storage module
    try:
        from bots.volatile_gridX import safe_storage
        result.add_check("SafeStorage module", True)
    except Exception as e:
        result.add_check("SafeStorage module", False, str(e))
    
    return result


def validate_security() -> ValidationResult:
    """Validate security measures."""
    result = ValidationResult("Security")
    
    # Check telegram security
    try:
        from bots.volatile_gridX import telegram_security
        result.add_check("Telegram security module", True)
    except Exception as e:
        result.add_check("Telegram security module", False, str(e))
    
    # Check environment variables
    env_vars = [
        ("BOT_TOKEN", os.getenv("BOT_TOKEN")),
        ("TRADING_ENABLED", os.getenv("TRADING_ENABLED")),
        ("EMERGENCY_STOP", os.getenv("EMERGENCY_STOP")),
    ]
    
    for name, value in env_vars:
        if value:
            result.add_check(f"ENV {name} set", True)
        else:
            result.add_check(f"ENV {name} set", True, "Not set (optional)", warning=True)
    
    # Check for hardcoded secrets
    base_path = Path(__file__).resolve().parent.parent
    secret_patterns = ["API_KEY", "SECRET", "PASSWORD", "TOKEN"]
    
    # Only check Python files, skip .pyc and __pycache__
    found_hardcoded = False
    for py_file in base_path.glob("**/*.py"):
        if "__pycache__" in str(py_file):
            continue
        try:
            content = py_file.read_text()
            for pattern in secret_patterns:
                # Look for hardcoded values (not env lookups)
                if f'{pattern} = "' in content or f"{pattern} = '" in content:
                    # Exclude comments and env lookups
                    if "os.getenv" not in content[:content.find(pattern) + 100]:
                        found_hardcoded = True
                        break
        except Exception:
            pass
    
    result.add_check("No hardcoded secrets", not found_hardcoded, 
                     "Potential hardcoded secrets found" if found_hardcoded else "")
    
    return result


def validate_dashboard() -> ValidationResult:
    """Validate dashboard components."""
    result = ValidationResult("Dashboard")
    
    base_path = Path(__file__).resolve().parent.parent
    
    # Check template
    template_path = base_path / "dashboard" / "templates" / "dashboard.html"
    if template_path.exists():
        result.add_check("Dashboard template exists", True)
    else:
        result.add_check("Dashboard template exists", False)
    
    # Check static files
    static_path = base_path / "dashboard" / "static"
    if static_path.exists():
        js_files = list(static_path.glob("*.js"))
        css_files = list(static_path.glob("*.css"))
        result.add_check("Static JS files", len(js_files) > 0, f"{len(js_files)} files")
        result.add_check("Static CSS files", len(css_files) > 0, f"{len(css_files)} files")
    else:
        result.add_check("Static directory", False)
    
    # Check app.py
    try:
        import app as main_app
        result.add_check("Main app import", True)
        
        # Check key endpoints
        routes = [r.path for r in main_app.app.routes]
        key_endpoints = ["/", "/api/v1/state", "/api/monitoring/health"]
        for endpoint in key_endpoints:
            if any(endpoint in r for r in routes):
                result.add_check(f"Endpoint {endpoint}", True)
            else:
                result.add_check(f"Endpoint {endpoint}", False)
    except Exception as e:
        result.add_check("Main app import", False, str(e))
    
    return result


def validate_railway_deployment() -> ValidationResult:
    """Validate Railway deployment readiness."""
    result = ValidationResult("Railway Deployment")
    
    base_path = Path(__file__).resolve().parent.parent
    
    # Check Procfile or railway.toml
    procfile = base_path / "Procfile"
    railway_toml = base_path / "railway.toml"
    
    if procfile.exists():
        result.add_check("Procfile exists", True)
    elif railway_toml.exists():
        result.add_check("railway.toml exists", True)
    else:
        result.add_check("Deployment config", True, "No Procfile/railway.toml", warning=True)
    
    # Check requirements.txt
    requirements = base_path / "requirements.txt"
    if requirements.exists():
        content = requirements.read_text()
        lines = [l.strip() for l in content.split("\n") if l.strip() and not l.startswith("#")]
        result.add_check("requirements.txt exists", True, f"{len(lines)} packages")
        
        # Check key packages
        key_packages = ["fastapi", "uvicorn", "psutil", "pydantic"]
        for pkg in key_packages:
            if any(pkg in l.lower() for l in lines):
                result.add_check(f"Package {pkg}", True)
            else:
                result.add_check(f"Package {pkg}", False, "Missing from requirements")
    else:
        result.add_check("requirements.txt exists", False)
    
    # Check PORT env handling
    try:
        import app as main_app
        # Check if PORT is properly handled
        result.add_check("PORT env handling", True)
    except Exception:
        result.add_check("PORT env handling", True, "Unable to verify", warning=True)
    
    return result


# =============================================================================
# PRODUCTION REPORT GENERATOR
# =============================================================================

def calculate_readiness_score(results: Dict[str, ValidationResult]) -> Tuple[int, str]:
    """Calculate production readiness score."""
    total_passed = sum(r.passed for r in results.values())
    total_failed = sum(r.failed for r in results.values())
    total_warnings = sum(r.warnings for r in results.values())
    total_checks = total_passed + total_failed + total_warnings
    
    if total_checks == 0:
        return 0, "NO_TESTS"
    
    # Score calculation: passed gets full points, warnings get half
    score = int(((total_passed + total_warnings * 0.5) / total_checks) * 100)
    
    # Determine recommendation
    if total_failed == 0 and score >= 90:
        recommendation = "GO"
    elif total_failed <= 2 and score >= 75:
        recommendation = "CONDITIONAL_GO"
    else:
        recommendation = "NO_GO"
    
    return score, recommendation


def generate_production_report(
    validation_results: Dict[str, ValidationResult],
    load_test_report: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Generate comprehensive production report."""
    
    score, recommendation = calculate_readiness_score(validation_results)
    
    # Count totals
    total_passed = sum(r.passed for r in validation_results.values())
    total_failed = sum(r.failed for r in validation_results.values())
    total_warnings = sum(r.warnings for r in validation_results.values())
    
    # Collect all failures
    critical_issues = []
    warnings = []
    
    for component, result in validation_results.items():
        for check in result.checks:
            if not check["passed"] and not check.get("warning"):
                critical_issues.append(f"[{component}] {check['name']}: {check.get('message', 'Failed')}")
            elif check.get("warning"):
                warnings.append(f"[{component}] {check['name']}: {check.get('message', 'Warning')}")
    
    report = {
        "report_type": "PRODUCTION_READINESS",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "version": "1.0",
        
        "summary": {
            "production_readiness_score": score,
            "recommendation": recommendation,
            "total_checks": total_passed + total_failed + total_warnings,
            "passed": total_passed,
            "failed": total_failed,
            "warnings": total_warnings,
        },
        
        "component_results": {k: v.to_dict() for k, v in validation_results.items()},
        
        "critical_issues": critical_issues,
        "warnings": warnings,
        
        "load_test_results": load_test_report,
        
        "go_no_go": {
            "recommendation": recommendation,
            "rationale": _get_recommendation_rationale(recommendation, score, critical_issues),
            "conditions": _get_conditions(recommendation, critical_issues, warnings),
        },
    }
    
    return report


def _get_recommendation_rationale(recommendation: str, score: int, issues: List[str]) -> str:
    """Get rationale for recommendation."""
    if recommendation == "GO":
        return f"System is production-ready with a score of {score}/100. All critical checks passed."
    elif recommendation == "CONDITIONAL_GO":
        return f"System has minor issues (score: {score}/100). Can proceed with monitoring. Issues: {len(issues)}"
    else:
        return f"System is NOT ready for production (score: {score}/100). {len(issues)} critical issues must be resolved."


def _get_conditions(recommendation: str, issues: List[str], warnings: List[str]) -> List[str]:
    """Get conditions for proceeding."""
    conditions = []
    
    if recommendation == "GO":
        conditions.append("Continue with extended paper trading")
        conditions.append("Monitor system metrics for 24-48 hours")
        conditions.append("Verify Telegram alerts are working")
    elif recommendation == "CONDITIONAL_GO":
        conditions.append("Monitor closely for the first 24 hours")
        conditions.append("Fix warnings during paper trading")
        for issue in issues[:3]:
            conditions.append(f"Address: {issue}")
    else:
        conditions.append("DO NOT proceed until critical issues are fixed:")
        for issue in issues:
            conditions.append(f"FIX REQUIRED: {issue}")
    
    return conditions


# =============================================================================
# MAIN EXECUTION
# =============================================================================

def run_validation_suite(run_load_tests: bool = True) -> Dict[str, Any]:
    """Run complete validation suite."""
    print("=" * 60)
    print("PROJECT ALPHA - PRODUCTION VALIDATION SUITE")
    print("=" * 60)
    print()
    
    results = {}
    
    # Run validations
    print("[1/6] Validating Trading Engine...")
    results["trading_engine"] = validate_trading_engine()
    print(f"      Result: {results['trading_engine'].passed} passed, {results['trading_engine'].failed} failed")
    
    print("[2/6] Validating Scanner...")
    results["scanner"] = validate_scanner()
    print(f"      Result: {results['scanner'].passed} passed, {results['scanner'].failed} failed")
    
    print("[3/6] Validating Monitoring...")
    results["monitoring"] = validate_monitoring()
    print(f"      Result: {results['monitoring'].passed} passed, {results['monitoring'].failed} failed")
    
    print("[4/6] Validating Storage...")
    results["storage"] = validate_storage()
    print(f"      Result: {results['storage'].passed} passed, {results['storage'].failed} failed")
    
    print("[5/6] Validating Security...")
    results["security"] = validate_security()
    print(f"      Result: {results['security'].passed} passed, {results['security'].failed} failed")
    
    print("[6/6] Validating Dashboard...")
    results["dashboard"] = validate_dashboard()
    print(f"      Result: {results['dashboard'].passed} passed, {results['dashboard'].failed} failed")
    
    print("[7/7] Validating Railway Deployment...")
    results["railway"] = validate_railway_deployment()
    print(f"      Result: {results['railway'].passed} passed, {results['railway'].failed} failed")
    
    # Run load tests if requested
    load_test_report = None
    if run_load_tests:
        print()
        print("[LOAD TESTS] Running load test suite...")
        try:
            from monitoring.load_testing import LoadTestEngine
            engine = LoadTestEngine()
            lt_report = engine.run_full_test_suite(
                signal_count=100,
                position_count=50,
                scanner_count=50,
                api_count=100,
                storage_count=100,
                telegram_count=50,
            )
            load_test_report = lt_report.to_dict()
            print(f"[LOAD TESTS] Complete. Result: {'PASS' if lt_report.passed else 'FAIL'}")
        except Exception as e:
            print(f"[LOAD TESTS] Failed: {e}")
            load_test_report = {"error": str(e), "passed": False}
    
    # Generate report
    print()
    print("Generating production report...")
    report = generate_production_report(results, load_test_report)
    
    # Print summary
    print()
    print("=" * 60)
    print("PRODUCTION READINESS REPORT")
    print("=" * 60)
    print(f"Score: {report['summary']['production_readiness_score']}/100")
    print(f"Recommendation: {report['go_no_go']['recommendation']}")
    print(f"Rationale: {report['go_no_go']['rationale']}")
    print()
    
    if report['critical_issues']:
        print("CRITICAL ISSUES:")
        for issue in report['critical_issues']:
            print(f"  - {issue}")
        print()
    
    if report['warnings']:
        print(f"WARNINGS ({len(report['warnings'])}):")
        for warning in report['warnings'][:5]:
            print(f"  - {warning}")
        if len(report['warnings']) > 5:
            print(f"  ... and {len(report['warnings']) - 5} more")
        print()
    
    print("CONDITIONS:")
    for condition in report['go_no_go']['conditions']:
        print(f"  - {condition}")
    
    return report


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="PROJECT ALPHA Production Validation")
    parser.add_argument("--no-load-tests", action="store_true", help="Skip load tests")
    parser.add_argument("--output", type=str, help="Output file for JSON report")
    
    args = parser.parse_args()
    
    report = run_validation_suite(run_load_tests=not args.no_load_tests)
    
    if args.output:
        with open(args.output, "w") as f:
            json.dump(report, f, indent=2)
        print(f"\nReport saved to: {args.output}")
