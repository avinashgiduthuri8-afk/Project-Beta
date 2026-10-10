"""
PROJECT-ALPHA — Background Module.

Provides async task scheduling, health monitoring, performance analytics,
backtesting pipelines, and self-calibrating feedback loops.
"""

from .ai.service import AIIntelligenceService
from .analytics.engine import AnalyticsEngine
from .backtest.engine import BacktestEngine
from .backtest.service.service import BacktestService
from .feedback.orchestrator import FeedbackOrchestrator
from .feedback.service import FeedbackService
from .journal.service import JournalService
from .learning.engine import LearningEngine
from .learning.service import LearningService
from .monitoring.health import HealthChecker
from .portfolio.service import PortfolioService
from .production.controller import DeploymentMode, ProductionController
from .production.service import ProductionService
from .production.watchdog import ProductionWatchdog
from .scheduler.scheduler import BackgroundScheduler

# Aliases
Scheduler = BackgroundScheduler

__all__ = [
    "AIIntelligenceService",
    "AnalyticsEngine",
    "BackgroundScheduler",
    "BacktestEngine",
    "BacktestService",
    "DeploymentMode",
    "FeedbackOrchestrator",
    "FeedbackService",
    "HealthChecker",
    "JournalService",
    "LearningEngine",
    "LearningService",
    "PortfolioService",
    "ProductionController",
    "ProductionService",
    "ProductionWatchdog",
    "Scheduler",
]
