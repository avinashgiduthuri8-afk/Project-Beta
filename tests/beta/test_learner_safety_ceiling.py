import pytest

from learner.safety import CalibrationSafety


def test_safe_calibration_is_accepted_whole():
    decision = CalibrationSafety().validate(
        {"strategy": "STE", "weight_multiplier": "1.10", "confluence_threshold": "85"},
        {"strategy": "STE", "weight_multiplier": "1.00", "confluence_threshold": "90"},
    )
    assert decision.accepted is True
    assert decision.code == "CALIBRATION_SAFE"


@pytest.mark.parametrize(
    "candidate",
    [
        {"strategy": "STE", "order_size_inr": 2000},
        {"strategy": "STE", "daily_loss_limit_inr": 2000},
        {"strategy": "STE", "price_ceiling_inr": 500},
        {"strategy": "STE", "leverage": 2},
        {"strategy": "STE", "weight_multiplier": "NaN"},
        {"strategy": "STE", "weight_multiplier": "1.21"},
        {"strategy": "STE", "confluence_threshold": "84.99"},
    ],
)
def test_unsafe_calibration_retains_last_safe(candidate):
    last_safe = {"strategy": "STE", "weight_multiplier": "1.00", "confluence_threshold": "90"}
    decision = CalibrationSafety().validate(candidate, last_safe)
    assert decision.accepted is False
    assert decision.candidate == last_safe
