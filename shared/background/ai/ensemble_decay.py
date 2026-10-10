"""
PROJECT-ALPHA — Multi-Model Ensemble Weight Decay Engine (Skill 24).

Dynamically adjusts signal evaluation model weights based on prediction accuracy and outcome feedback:
  1. Multi-Model Score Weighting: C2 Technical Scanner, Pattern Engine, AI Verifier
  2. Exponential Weight Decay: Reduces weights of underperforming models on false positives
  3. Dynamic Recovery & Renormalization: Bounds weights [0.10, 0.60] and enforces sum(w_i) = 1.0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.logging import get_logger

logger = get_logger("background.ai.ensemble_decay")

DEFAULT_MODEL_KEYS = ["c2_scanner", "pattern_engine", "ai_verifier"]


@dataclass
class ModelWeightMap:
    """Current ensemble weights and performance telemetry."""

    weights: dict[str, float]
    total_evaluations: int = 0
    decay_counts: dict[str, int] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)


class EnsembleWeightDecayer:
    """
    Dynamic weight decayer for multi-model signal evaluation ensembles.
    """

    def __init__(
        self,
        base_weights: dict[str, float] | None = None,
        decay_factor: float = 0.90,  # 10% weight penalty on false positive / loss
        recovery_factor: float = 1.05,  # 5% weight boost on confirmed win
        min_weight: float = 0.10,
        max_weight: float = 0.60,
    ) -> None:
        self.decay_factor = decay_factor
        self.recovery_factor = recovery_factor
        self.min_weight = min_weight
        self.max_weight = max_weight

        # Default balanced ensemble: C2: 0.40, Pattern: 0.35, AI: 0.25
        self._weights = base_weights or {
            "c2_scanner": 0.40,
            "pattern_engine": 0.35,
            "ai_verifier": 0.25,
        }
        self._normalize_weights()
        self._total_evaluations = 0
        self._decay_counts = {k: 0 for k in self._weights}

    def _normalize_weights(self) -> None:
        """
        Clamps weights into [min_weight, max_weight] and renormalizes sum to 1.0.
        """
        # 1. Clamp bounds
        clamped = {
            k: max(self.min_weight, min(self.max_weight, w))
            for k, w in self._weights.items()
        }
        total = sum(clamped.values())
        if total <= 0:
            count = max(1, len(clamped))
            self._weights = {k: round(1.0 / count, 4) for k in clamped}
            return

        # 2. Normalize
        self._weights = {k: round(v / total, 4) for k, v in clamped.items()}

    def get_current_weights(self) -> dict[str, float]:
        """Return a copy of active ensemble weights."""
        return dict(self._weights)

    def record_outcome(self, model_key: str, is_correct: bool) -> ModelWeightMap:
        """
        Update model weight based on trade outcome or validation check.
        """
        if model_key not in self._weights:
            logger.warning("Unknown model key '%s' passed to EnsembleWeightDecayer", model_key)
            return self.get_telemetry()

        self._total_evaluations += 1

        if is_correct:
            self._weights[model_key] *= self.recovery_factor
        else:
            self._weights[model_key] *= self.decay_factor
            self._decay_counts[model_key] = self._decay_counts.get(model_key, 0) + 1
            logger.info(
                "Applied weight decay to model '%s' (new raw weight: %.4f)",
                model_key,
                self._weights[model_key],
            )

        self._normalize_weights()
        return self.get_telemetry()

    def calculate_weighted_score(
        self, model_scores: dict[str, float]
    ) -> tuple[float, ModelWeightMap]:
        """
        Calculates composite weighted ensemble score given individual model scores.
        """
        weighted_sum = 0.0
        used_weight_sum = 0.0

        for key, score in model_scores.items():
            if key in self._weights:
                w = self._weights[key]
                weighted_sum += w * score
                used_weight_sum += w

        if used_weight_sum <= 0:
            final_score = 0.0
        else:
            # Rescale if partial models provided
            final_score = weighted_sum / used_weight_sum

        return round(final_score, 1), self.get_telemetry()

    def reset_weights(self) -> None:
        """Reset ensemble weights to default baseline."""
        self._weights = {
            "c2_scanner": 0.40,
            "pattern_engine": 0.35,
            "ai_verifier": 0.25,
        }
        self._normalize_weights()

    def get_telemetry(self) -> ModelWeightMap:
        return ModelWeightMap(
            weights=self.get_current_weights(),
            total_evaluations=self._total_evaluations,
            decay_counts=dict(self._decay_counts),
        )

