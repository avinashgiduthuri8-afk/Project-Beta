"""
PROJECT-ALPHA — Unit Tests for Multi-Model Ensemble Weight Decay (Skill 24).
"""

from __future__ import annotations

import pytest

from background.ai.ensemble_decay import EnsembleWeightDecayer, ModelWeightMap


def test_ensemble_weight_decayer_initialization():
    decayer = EnsembleWeightDecayer()
    weights = decayer.get_current_weights()

    assert "c2_scanner" in weights
    assert "pattern_engine" in weights
    assert "ai_verifier" in weights
    assert sum(weights.values()) == pytest.approx(1.0, abs=1e-3)


def test_ensemble_weight_decay_on_failure():
    decayer = EnsembleWeightDecayer(decay_factor=0.80)
    initial_c2_weight = decayer.get_current_weights()["c2_scanner"]

    # Record repeated failures for c2_scanner
    for _ in range(3):
        decayer.record_outcome("c2_scanner", is_correct=False)

    new_weights = decayer.get_current_weights()
    # c2_scanner weight should decrease relative to other models
    assert new_weights["c2_scanner"] < initial_c2_weight
    assert sum(new_weights.values()) == pytest.approx(1.0, abs=1e-3)


def test_ensemble_weight_bounds_clamping():
    decayer = EnsembleWeightDecayer(min_weight=0.10, max_weight=0.60)

    # Record 20 consecutive failures for ai_verifier
    for _ in range(20):
        decayer.record_outcome("ai_verifier", is_correct=False)

    weights = decayer.get_current_weights()
    # Should not drop below min_weight after normalization
    assert weights["ai_verifier"] >= 0.08  # Normalized min bound check
    assert sum(weights.values()) == pytest.approx(1.0, abs=1e-3)


def test_calculate_weighted_score():
    decayer = EnsembleWeightDecayer(
        base_weights={"c2_scanner": 0.50, "pattern_engine": 0.30, "ai_verifier": 0.20}
    )

    model_scores = {
        "c2_scanner": 90.0,
        "pattern_engine": 80.0,
        "ai_verifier": 70.0,
    }

    # Expected score: 0.50*90 + 0.30*80 + 0.20*70 = 45 + 24 + 14 = 83.0
    score, telemetry = decayer.calculate_weighted_score(model_scores)
    assert score == 83.0
    assert isinstance(telemetry, ModelWeightMap)

