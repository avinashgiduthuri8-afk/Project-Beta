"""Sector Momentum & Relative Strength Ranking Strategy."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Any
import numpy as np

from sector_quant.strategies.base import Strategy
from sector_quant.strategies.math_utils import calculate_relative_strength
from sector_quant.data.base import DataHandler
from sector_quant.events.events import MarketEvent, SignalEvent, SignalType
from sector_quant.events.queue import EventQueue

logger = logging.getLogger(__name__)


@dataclass
class SectorUniverseConfig:
    sector_code: str
    benchmark_symbol: str
    constituents: List[str]
    lookback_window: int = 20
    top_n: int = 2
    min_relative_strength: float = 0.01  # Minimum outperformance over benchmark (1%)
    rebalance_frequency: int = 5         # Check rebalance every N bars


class SectorMomentumStrategy(Strategy):
    """Ranks sector constituents by relative strength vs benchmark and rotates into leaders."""

    def __init__(
        self,
        bars: DataHandler,
        events_queue: EventQueue,
        sector_config: SectorUniverseConfig,
        strategy_id: str = "SECTOR_MOMENTUM",
    ):
        super().__init__(bars=bars, events_queue=events_queue, strategy_id=strategy_id)
        self.cfg = sector_config
        self.held_symbols: Set[str] = set()
        self.bar_counter: int = 0
        self.ranking_history: List[Dict[str, Any]] = []

    def calculate_signals(self, event: MarketEvent) -> None:
        """Evaluates relative strength ranking and emits rotational LONG and EXIT signals."""
        self.bar_counter += 1
        current_dt = event.datetime or self.bars.get_latest_bar_datetime(self.cfg.benchmark_symbol)
        if current_dt is None:
            return

        # Check if enough bars have elapsed for lookback window
        bench_prices = self.bars.get_latest_bars_values(
            self.cfg.benchmark_symbol, val_type="close", N=self.cfg.lookback_window + 1
        )
        if len(bench_prices) < self.cfg.lookback_window + 1:
            return

        # Periodic rebalance check
        if self.bar_counter % self.cfg.rebalance_frequency != 0:
            return

        # Calculate relative strength for all constituents
        scores: Dict[str, float] = {}
        for sym in self.cfg.constituents:
            stock_prices = self.bars.get_latest_bars_values(
                sym, val_type="close", N=self.cfg.lookback_window + 1
            )
            if len(stock_prices) < self.cfg.lookback_window + 1:
                continue

            rs = calculate_relative_strength(
                asset_prices=stock_prices,
                benchmark_prices=bench_prices,
                lookback=self.cfg.lookback_window,
            )
            scores[sym] = rs

        if not scores:
            return

        # Sort constituents by Relative Strength descending
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        top_candidates = [sym for sym, score in ranked[: self.cfg.top_n] if score >= self.cfg.min_relative_strength]

        self.ranking_history.append({
            "datetime": current_dt,
            "ranked": ranked,
            "selected": top_candidates,
        })

        # 1. Exit positions no longer in top candidates
        exited = set()
        for sym in list(self.held_symbols):
            if sym not in top_candidates:
                logger.info(f"[{current_dt}] EXIT MOMENTUM for {sym} (Dropped from top rank)")
                self.emit_signal(
                    symbol=sym,
                    signal_type=SignalType.EXIT,
                    datetime=current_dt,
                    meta={"sector": self.cfg.sector_code, "reason": "Dropped from top rank"},
                )
                exited.add(sym)

        self.held_symbols.difference_update(exited)

        # 2. Enter new top candidates
        for sym in top_candidates:
            if sym not in self.held_symbols:
                score = scores[sym]
                logger.info(f"[{current_dt}] LONG MOMENTUM trigger for {sym} (RS={score*100:.2f}%)")
                self.emit_signal(
                    symbol=sym,
                    signal_type=SignalType.LONG,
                    datetime=current_dt,
                    strength=1.0,
                    meta={"sector": self.cfg.sector_code, "rs_score": score},
                )
                self.held_symbols.add(sym)

