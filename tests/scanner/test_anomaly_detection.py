"""
PROJECT-ALPHA — Anomaly Detection Engine Unit & Integration Tests (Skill 20).
"""

from __future__ import annotations

import pytest

from scanner.anomaly_detector import AnomalyDetector, AnomalyReport, AnomalyType


@pytest.fixture
def detector() -> AnomalyDetector:
    return AnomalyDetector(
        volume_zscore_threshold=3.5,
        max_wick_ratio=0.75,
        max_range_atr_multiplier=4.0,
        max_momentum_pct=25.0,
    )


def test_normal_volume_no_anomaly(detector: AnomalyDetector):
    volumes = [10.0, 11.0, 9.5, 10.2, 10.8, 9.8, 10.1]
    is_spike, z_score = detector.calculate_volume_zscore(volumes)
    assert is_spike is False
    assert abs(z_score) < 2.0


def test_volume_spike_zscore_detected(detector: AnomalyDetector):
    # Steady volumes around 10.0, sudden spike to 100.0
    volumes = [10.0, 10.2, 9.8, 10.1, 10.0, 10.3, 9.9, 10.0, 100.0]
    is_spike, z_score = detector.calculate_volume_zscore(volumes)
    assert is_spike is True
    assert z_score >= 3.5


def test_extreme_upper_wick_detected(detector: AnomalyDetector):
    # Candle with huge upper wick: Open 100, Close 105, Low 99, High 150
    # Range: 51, Body: 100-105, Upper wick: 150 - 105 = 45 -> 45/51 = 88.2%
    candle = {
        "open": 100.0,
        "high": 150.0,
        "low": 99.0,
        "close": 105.0,
        "volume": 20.0,
    }
    is_anomaly, wick_type, ratio = detector.evaluate_wick_anomaly(candle)
    assert is_anomaly is True
    assert wick_type == AnomalyType.EXTREME_UPPER_WICK
    assert ratio >= 0.75


def test_extreme_lower_wick_detected(detector: AnomalyDetector):
    # Candle with huge lower wick: Open 100, Close 102, High 103, Low 60
    # Range: 43, Body: 100-102, Lower wick: 100 - 60 = 40 -> 40/43 = 93.0%
    candle = {
        "open": 100.0,
        "high": 103.0,
        "low": 60.0,
        "close": 102.0,
        "volume": 20.0,
    }
    is_anomaly, wick_type, ratio = detector.evaluate_wick_anomaly(candle)
    assert is_anomaly is True
    assert wick_type == AnomalyType.EXTREME_LOWER_WICK
    assert ratio >= 0.75


def test_range_expansion_blowout(detector: AnomalyDetector):
    # Historical normal ranges ~ 2.0
    recent_ranges = [2.0, 2.1, 1.9, 2.2, 2.0, 1.8]
    # Candle with range 12.0 (6x baseline)
    candle = {
        "open": 100.0,
        "high": 112.0,
        "low": 100.0,
        "close": 110.0,
    }
    is_blowout, multiplier = detector.evaluate_range_blowout(candle, recent_ranges)
    assert is_blowout is True
    assert multiplier >= 4.0


def test_full_series_evaluation(detector: AnomalyDetector):
    # Series with normal historical candles and final blowout candle
    candles = [
        {"open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 10.0}
        for _ in range(10)
    ]
    # Append abnormal candle
    candles.append(
        {"open": 101.0, "high": 150.0, "low": 100.0, "close": 105.0, "volume": 150.0}
    )

    report = detector.evaluate_series(candles)
    assert report.is_anomalous is True
    assert AnomalyType.VOLUME_ZSCORE_SPIKE in report.anomalies_detected
    assert AnomalyType.EXTREME_UPPER_WICK in report.anomalies_detected
    assert report.risk_penalty > 0.50
