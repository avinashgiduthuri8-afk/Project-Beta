"""Intra-Sector Cointegration & Rolling OLS Pairs Trading Strategy."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple, Any
import numpy as np

from sector_quant.strategies.base import Strategy
from sector_quant.strategies.math_utils import calculate_spread_and_zscore
from sector_quant.data.base import DataHandler
from sector_quant.events.events import MarketEvent, SignalEvent, SignalType
from sector_quant.events.queue import EventQueue

logger = logging.getLogger(__name__)


class PairPositionState(str, Enum):
    FLAT = "FLAT"
    LONG_SPREAD = "LONG_SPREAD"    # Long Y, Short X
    SHORT_SPREAD = "SHORT_SPREAD"  # Short Y, Long X


@dataclass
class PairConfig:
    symbol_y: str
    symbol_x: str
    sector: str = "GENERAL"
    ols_window: int = 60
    z_lookback: int = 30
    z_entry: float = 2.0
    z_exit: float = 0.5
    z_stop: float = 3.5
    state: PairPositionState = PairPositionState.FLAT
    last_beta: float = 1.0
    last_alpha: float = 0.0
    last_z_score: float = 0.0


class SectorPairsTradingStrategy(Strategy):
    """Event-driven intra-sector cointegration & rolling OLS pairs trading strategy."""

    def __init__(
        self,
        bars: DataHandler,
        events_queue: EventQueue,
        pairs: List[PairConfig],
        strategy_id: str = "SECTOR_PAIRS",
    ):
        super().__init__(bars=bars, events_queue=events_queue, strategy_id=strategy_id)
        self.pairs: Dict[Tuple[str, str], PairConfig] = {
            (p.symbol_y.upper(), p.symbol_x.upper()): p for p in pairs
        }
        self.pair_history: Dict[Tuple[str, str], List[Dict[str, Any]]] = {
            (p.symbol_y.upper(), p.symbol_x.upper()): [] for p in pairs
        }

    def calculate_signals(self, event: MarketEvent) -> None:
        """Evaluates spread z-scores for all pairs on each MarketEvent and emits paired signals."""
        current_dt = event.datetime or self.bars.get_latest_bar_datetime(list(self.pairs.keys())[0][0])
        if current_dt is None:
            return

        for (sym_y, sym_x), cfg in self.pairs.items():
            y_prices = self.bars.get_latest_bars_values(sym_y, val_type="close", N=cfg.ols_window)
            x_prices = self.bars.get_latest_bars_values(sym_x, val_type="close", N=cfg.ols_window)

            if len(y_prices) < cfg.ols_window or len(x_prices) < cfg.ols_window:
                continue

            calc_result = calculate_spread_and_zscore(
                y_series=y_prices,
                x_series=x_prices,
                z_lookback=cfg.z_lookback,
            )

            beta = calc_result["beta"]
            alpha = calc_result["alpha"]
            z = calc_result["z_score"]

            cfg.last_beta = beta
            cfg.last_alpha = alpha
            cfg.last_z_score = z

            self.pair_history[(sym_y, sym_x)].append({
                "datetime": current_dt,
                "y_price": y_prices[-1],
                "x_price": x_prices[-1],
                "beta": beta,
                "alpha": alpha,
                "spread": calc_result["latest_spread"],
                "z_score": z,
                "state": cfg.state.value,
            })

            # Check Signal Rules
            if cfg.state == PairPositionState.FLAT:
                # 1. Spread overvalued -> Short Spread (Short Y, Long X)
                if z >= cfg.z_entry:
                    cfg.state = PairPositionState.SHORT_SPREAD
                    logger.info(f"[{current_dt}] SHORT SPREAD trigger for ({sym_y}, {sym_x}) at z={z:.2f}, beta={beta:.3f}")
                    self.emit_signal(
                        symbol=sym_y,
                        signal_type=SignalType.SHORT,
                        datetime=current_dt,
                        strength=1.0,
                        meta={"pair_symbol": sym_x, "hedge_ratio": beta, "z_score": z, "sector": cfg.sector, "leg": "Y"},
                    )
                    self.emit_signal(
                        symbol=sym_x,
                        signal_type=SignalType.LONG,
                        datetime=current_dt,
                        strength=beta,
                        meta={"pair_symbol": sym_y, "hedge_ratio": beta, "z_score": z, "sector": cfg.sector, "leg": "X"},
                    )

                # 2. Spread undervalued -> Long Spread (Long Y, Short X)
                elif z <= -cfg.z_entry:
                    cfg.state = PairPositionState.LONG_SPREAD
                    logger.info(f"[{current_dt}] LONG SPREAD trigger for ({sym_y}, {sym_x}) at z={z:.2f}, beta={beta:.3f}")
                    self.emit_signal(
                        symbol=sym_y,
                        signal_type=SignalType.LONG,
                        datetime=current_dt,
                        strength=1.0,
                        meta={"pair_symbol": sym_x, "hedge_ratio": beta, "z_score": z, "sector": cfg.sector, "leg": "Y"},
                    )
                    self.emit_signal(
                        symbol=sym_x,
                        signal_type=SignalType.SHORT,
                        datetime=current_dt,
                        strength=beta,
                        meta={"pair_symbol": sym_y, "hedge_ratio": beta, "z_score": z, "sector": cfg.sector, "leg": "X"},
                    )

            elif cfg.state == PairPositionState.SHORT_SPREAD:
                # Exit on mean reversion or stop loss
                if z <= cfg.z_exit or z >= cfg.z_stop:
                    reason = "Mean Reversion" if z <= cfg.z_exit else "Stop Loss Divergence"
                    logger.info(f"[{current_dt}] EXIT SHORT SPREAD ({reason}) for ({sym_y}, {sym_x}) at z={z:.2f}")
                    cfg.state = PairPositionState.FLAT
                    self.emit_signal(
                        symbol=sym_y,
                        signal_type=SignalType.EXIT,
                        datetime=current_dt,
                        meta={"pair_symbol": sym_x, "reason": reason, "z_score": z, "sector": cfg.sector},
                    )
                    self.emit_signal(
                        symbol=sym_x,
                        signal_type=SignalType.EXIT,
                        datetime=current_dt,
                        meta={"pair_symbol": sym_y, "reason": reason, "z_score": z, "sector": cfg.sector},
                    )

            elif cfg.state == PairPositionState.LONG_SPREAD:
                # Exit on mean reversion or stop loss
                if z >= -cfg.z_exit or z <= -cfg.z_stop:
                    reason = "Mean Reversion" if z >= -cfg.z_exit else "Stop Loss Divergence"
                    logger.info(f"[{current_dt}] EXIT LONG SPREAD ({reason}) for ({sym_y}, {sym_x}) at z={z:.2f}")
                    cfg.state = PairPositionState.FLAT
                    self.emit_signal(
                        symbol=sym_y,
                        signal_type=SignalType.EXIT,
                        datetime=current_dt,
                        meta={"pair_symbol": sym_x, "reason": reason, "z_score": z, "sector": cfg.sector},
                    )
                    self.emit_signal(
                        symbol=sym_x,
                        signal_type=SignalType.EXIT,
                        datetime=current_dt,
                        meta={"pair_symbol": sym_y, "reason": reason, "z_score": z, "sector": cfg.sector},
                    )

