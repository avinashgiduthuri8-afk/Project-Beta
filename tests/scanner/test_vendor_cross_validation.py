"""
PROJECT-ALPHA — Unit Tests for Data-Vendor Cross-Validation (Skill 18).
"""

from __future__ import annotations

import pytest

from background.backtest.vendor_cross_val import (
    CrossValidationReport,
    VendorCrossValidator,
)


def test_vendor_cross_validation_clean_matching_series():
    validator = VendorCrossValidator(max_price_variance_pct=0.50)

    primary = [
        {"timestamp": 1000 + i * 3600, "close": 100.0 + i} for i in range(20)
    ]
    # Secondary feed matching perfectly
    secondary = [
        {"timestamp": 1000 + i * 3600, "close": 100.0 + i} for i in range(20)
    ]

    report = validator.cross_validate_series(primary, secondary, pair="BTC/INR", timeframe="1h")

    assert isinstance(report, CrossValidationReport)
    assert report.is_valid is True
    assert report.reliability_score == 100.0
    assert report.total_bars_compared == 20
    assert report.price_variance_breaches == 0
    assert report.bad_ticks_count == 0


def test_vendor_cross_validation_variance_breaches():
    validator = VendorCrossValidator(max_price_variance_pct=0.50)

    primary = [
        {"timestamp": 1000 + i * 3600, "close": 100.0} for i in range(10)
    ]
    # Secondary feed has 2 bars with > 1% price variance
    secondary = [
        {"timestamp": 1000 + i * 3600, "close": 100.0 if i not in (3, 7) else 102.0}
        for i in range(10)
    ]

    report = validator.cross_validate_series(primary, secondary, pair="SOL/INR", timeframe="1h")

    assert report.total_bars_compared == 10
    assert report.price_variance_breaches == 2
    assert report.reliability_score == 80.0
    assert report.is_valid is False  # 80.0% < 95.0% min reliability
    assert len(report.anomalies) == 2


def test_vendor_cross_validation_timestamp_gaps():
    validator = VendorCrossValidator()

    primary = [
        {"timestamp": 1000 + i * 3600, "close": 500.0} for i in range(10)
    ]
    # Secondary feed missing bar at index 5
    secondary = [
        {"timestamp": 1000 + i * 3600, "close": 500.0} for i in range(10) if i != 5
    ]

    report = validator.cross_validate_series(primary, secondary, pair="ETH/INR", timeframe="1h")

    assert report.total_bars_compared == 9
    assert report.timestamp_discrepancies == 1
    assert any(a["reason"] == "MISSING_SECONDARY_TIMESTAMP" for a in report.anomalies)

