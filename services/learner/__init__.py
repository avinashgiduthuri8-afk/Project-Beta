"""
PROJECT-ALPHA — Learner Service Package (Stages 8–10).

Autonomous Learning Engine, Walk-Forward Pre-Validation, and Canary Parameter Calibrator.
"""

from __future__ import annotations

import sys
from pathlib import Path

_DIR = Path(__file__).resolve().parent
_SERVICES = _DIR.parent
_PROJECT_ROOT = _SERVICES.parent
_SHARED = _PROJECT_ROOT / "shared"

for _p in [_SHARED, _SERVICES, _PROJECT_ROOT]:
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from .config import LearnerConfig, get_learner_config
from .service import LearnerService

__all__ = ["LearnerConfig", "LearnerService", "get_learner_config"]
