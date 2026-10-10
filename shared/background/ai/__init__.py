"""
V2 AI Intelligence Service Package.

Exports AIIntelligenceService, GeminiClient, and FallbackEvaluator.
"""

from .client import GeminiClient
from .evaluator import FallbackEvaluator
from .prompt_templates import (
    AI_EVALUATION_SCHEMA,
    SYSTEM_INSTRUCTION,
    build_signal_prompt,
)
from .service import AIIntelligenceService

__all__ = [
    "AI_EVALUATION_SCHEMA",
    "SYSTEM_INSTRUCTION",
    "AIIntelligenceService",
    "FallbackEvaluator",
    "GeminiClient",
    "build_signal_prompt",
]
