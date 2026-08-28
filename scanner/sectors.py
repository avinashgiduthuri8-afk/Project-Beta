"""Sector Strength & Momentum Matrix for Indian Equities (Project-Beta)."""

from __future__ import annotations

import logging
from typing import Dict, List, Any

logger = logging.getLogger(__name__)

# Standard Sectoral Indices on NSE
NSE_SECTORS = [
    "NIFTY BANK", "NIFTY IT", "NIFTY AUTO", "NIFTY PHARMA",
    "NIFTY FMCG", "NIFTY METAL", "NIFTY ENERGY", "NIFTY INFRA"
]


class SectorStrengthAnalyzer:
    """Ranks and scores leading sectors relative to the benchmark NIFTY 50 index."""

    def __init__(self):
        # Simulated/live sector momentum metrics
        self.sector_performance: Dict[str, float] = {
            "IT": 1.4,
            "ENERGY": 1.1,
            "AUTO": 0.8,
            "BANKING": 0.5,
            "METALS": 0.2,
            "FMCG": -0.3,
            "PHARMA": -0.5,
            "INFRA": 0.1,
        }

    def get_leading_sectors(self) -> List[str]:
        """Returns top performing sectors sorted by relative strength."""
        sorted_sectors = sorted(self.sector_performance.items(), key=lambda x: x[1], reverse=True)
        return [sec[0] for sec in sorted_sectors if sec[1] > 0]

    def is_sector_in_momentum(self, sector: str) -> bool:
        """Check if stock's sector is in leading / outperforming regime."""
        return self.sector_performance.get(sector.upper(), 0.0) > 0.3
