"""Project Beta learner service (stages 8–10)."""

from .config import LearnerConfig, get_learner_config
from .indian_service import IndianLearnerService

__all__ = ["IndianLearnerService", "LearnerConfig", "get_learner_config"]
