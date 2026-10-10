"""
V2 CoinResearchService — On-Demand Coin Deep Dive & Research Hub.

Provides isolated, read-only analytics:
  - Multi-timeframe technical indicators (EMA, RSI, MACD, BB, ATR, RVOL)
  - Minervini VCP contraction stage detection
  - 100-Point 4-Pillar Quality Index scorecard
  - On-demand historical backtest via BacktestEngine
  - AI multi-horizon trend prediction (rule-based + optional Gemini enrichment)
"""

from .service import CoinResearchService

__all__ = ["CoinResearchService"]
