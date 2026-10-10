"""
tests/test_v2_learning_safety_boundary.py
Unit tests verifying B10 Learning Safety Boundary.
Asserts that learning_service cannot modify B2 filter thresholds, B5 max signal cap, or B7 single-coin lock.
"""

import pytest

from background.learning.calibrator import (
    IMMUTABLE_SAFETY_BOUNDARIES,
    StrategyCalibrator,
)


def test_immutable_safety_boundary_keys():
    """Verify essential risk gates and filter rules are declared in IMMUTABLE_SAFETY_BOUNDARIES."""
    expected_invariants = {
        "scanner_min_24h_volume",
        "scanner_max_price_change_pct",
        "scanner_min_atr_pct",
        "scanner_max_atr_pct",
        "v2_scanner_max_signals",
        "enforce_single_coin_lock",
        "order_size_inr",
        "total_capital_limit",
        "v2_max_drawdown_pct",
    }
    for inv in expected_invariants:
        assert inv in IMMUTABLE_SAFETY_BOUNDARIES


def test_validate_safety_boundary_raises_permission_error():
    """Verify validate_safety_boundary blocks any attempt to tamper with risk invariants."""
    # 1. Allowed calibration: weight multipliers, min score thresholds
    allowed = {
        "weight_multiplier": 1.1,
        "min_confluence_threshold": 88.0,
    }
    StrategyCalibrator.validate_safety_boundary(allowed)

    # 2. Tampering with max signals cap (B5) -> PermissionError
    with pytest.raises(PermissionError, match="Safety Violation"):
        StrategyCalibrator.validate_safety_boundary({"v2_scanner_max_signals": 10})

    # 3. Tampering with single-coin lock (B7) -> PermissionError
    with pytest.raises(PermissionError, match="Safety Violation"):
        StrategyCalibrator.validate_safety_boundary({"enforce_single_coin_lock": False})

    # 4. Tampering with B2 filter thresholds -> PermissionError
    with pytest.raises(PermissionError, match="Safety Violation"):
        StrategyCalibrator.validate_safety_boundary({"scanner_min_24h_volume": 0.0})

    with pytest.raises(PermissionError, match="Safety Violation"):
        StrategyCalibrator.validate_safety_boundary({"order_size_inr": 50000.0})
