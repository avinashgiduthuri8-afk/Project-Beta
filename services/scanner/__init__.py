"""
PROJECT-ALPHA Scanner Module — Multi-Timeframe Trend & Confluence Engine.
"""

# Keep package import side-effect free.  Service entrypoints establish the
# shared/services import paths before importing concrete scanner modules.
__all__ = [
    "CalibrationWorker",
    "ConfluenceEngine",
    "MarketContextService",
    "NewsRiskService",
    "ScannerService",
    "calculate_ema",
]


def __getattr__(name: str):
    if name == "CalibrationWorker":
        from .calibration_worker import CalibrationWorker

        return CalibrationWorker
    if name == "ConfluenceEngine":
        from .confluence_engine import ConfluenceEngine

        return ConfluenceEngine
    if name in {"MarketContextService", "calculate_ema"}:
        from .market_context import MarketContextService, calculate_ema

        return {"MarketContextService": MarketContextService, "calculate_ema": calculate_ema}[name]
    if name == "NewsRiskService":
        from .news_fetcher import NewsRiskService

        return NewsRiskService
    if name == "ScannerService":
        from .service import ScannerService

        return ScannerService
    raise AttributeError(name)
