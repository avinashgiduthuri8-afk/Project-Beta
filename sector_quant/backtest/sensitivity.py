"""Parameter Sensitivity and Transaction Cost Drag Analyzer."""

from __future__ import annotations

import logging
from typing import Dict, Any, List, Optional, Callable, Iterable
import pandas as pd

from sector_quant.data.historic_sector import HistoricSectorDataHandler
from sector_quant.events.queue import EventQueue
from sector_quant.portfolio.portfolio import SectorPortfolio
from sector_quant.execution.simulated import SimulatedExecutionHandler, ExecutionCostConfig, CommissionScheme
from sector_quant.backtest.engine import BacktestEngine

logger = logging.getLogger(__name__)


class SensitivityAnalyzer:
    """Evaluates strategy stability across parameter variations and transaction cost levels."""

    def __init__(
        self,
        data_dict: Dict[str, pd.DataFrame],
        initial_capital: float = 100_000.0,
    ):
        self.data_dict = data_dict
        self.initial_capital = initial_capital

    def evaluate_cost_drag(
        self,
        strategy_factory: Callable[[HistoricSectorDataHandler, EventQueue], Any],
        slippage_levels_bps: List[float] = [0.0, 2.0, 5.0, 10.0, 20.0],
        per_share_commissions: List[float] = [0.0, 0.005, 0.01],
    ) -> pd.DataFrame:
        """Audits alpha degradation across multiple transaction cost and slippage scenarios."""
        results = []

        for slip in slippage_levels_bps:
            for comm in per_share_commissions:
                cost_cfg = ExecutionCostConfig(
                    commission_scheme=CommissionScheme.PER_SHARE if comm > 0 else CommissionScheme.ZERO,
                    per_share_rate=comm,
                    min_commission=1.0 if comm > 0 else 0.0,
                    slippage_bps=slip,
                )

                queue = EventQueue()
                data_handler = HistoricSectorDataHandler(
                    events_queue=queue,
                    data_dict=self.data_dict,
                )
                strategy = strategy_factory(data_handler, queue)
                portfolio = SectorPortfolio(
                    bars=data_handler,
                    events_queue=queue,
                    initial_capital=self.initial_capital,
                )
                execution_handler = SimulatedExecutionHandler(
                    events_queue=queue,
                    bars=data_handler,
                    cost_config=cost_cfg,
                )
                engine = BacktestEngine(
                    data_handler=data_handler,
                    strategy=strategy,
                    portfolio=portfolio,
                    execution_handler=execution_handler,
                    events_queue=queue,
                )

                res = engine.run_backtest()
                results.append({
                    "slippage_bps": slip,
                    "commission_per_share": comm,
                    "total_return_pct": res.get("total_return_pct", 0.0),
                    "cagr_pct": res.get("cagr_pct", 0.0),
                    "sharpe_ratio": res.get("sharpe_ratio", 0.0),
                    "max_drawdown_pct": res.get("max_drawdown_pct", 0.0),
                    "total_trades": res.get("total_trades", 0),
                    "cumulative_commission": res.get("cumulative_commission", 0.0),
                })

        return pd.DataFrame(results)

    def evaluate_grid(
        self,
        strategy_builder: Callable[[HistoricSectorDataHandler, EventQueue, Dict[str, Any]], Any],
        param_grid: List[Dict[str, Any]],
        cost_config: Optional[ExecutionCostConfig] = None,
    ) -> pd.DataFrame:
        """Runs backtests over a list of parameter dictionaries and summarizes comparative metrics."""
        results = []
        cost_cfg = cost_config or ExecutionCostConfig()

        for idx, params in enumerate(param_grid):
            queue = EventQueue()
            data_handler = HistoricSectorDataHandler(
                events_queue=queue,
                data_dict=self.data_dict,
            )
            strategy = strategy_builder(data_handler, queue, params)
            portfolio = SectorPortfolio(
                bars=data_handler,
                events_queue=queue,
                initial_capital=self.initial_capital,
            )
            execution_handler = SimulatedExecutionHandler(
                events_queue=queue,
                bars=data_handler,
                cost_config=cost_cfg,
            )
            engine = BacktestEngine(
                data_handler=data_handler,
                strategy=strategy,
                portfolio=portfolio,
                execution_handler=execution_handler,
                events_queue=queue,
            )

            res = engine.run_backtest()
            row = dict(params)
            row.update({
                "total_return_pct": res.get("total_return_pct", 0.0),
                "cagr_pct": res.get("cagr_pct", 0.0),
                "sharpe_ratio": res.get("sharpe_ratio", 0.0),
                "sortino_ratio": res.get("sortino_ratio", 0.0),
                "max_drawdown_pct": res.get("max_drawdown_pct", 0.0),
                "win_rate_pct": res.get("win_rate_pct", 0.0),
                "profit_factor": res.get("profit_factor", 0.0),
                "total_trades": res.get("total_trades", 0),
            })
            results.append(row)

        return pd.DataFrame(results)

