"""
Fleet Selector & Ranking Gate for PROJECT-ALPHA.

Filters and ranks 10 candidate backtested strategies against strict production criteria:
  - Net Profit Factor >= 1.75 (After TDS + Fees)
  - Net R:R >= 1 : 1.50
  - Max Drawdown < 15.0%
  - Positive survival over statutory fee drag
"""

from __future__ import annotations

from dataclasses import dataclass

from .metrics import PerformanceMetrics


@dataclass
class StrategyRank:
    rank: int
    metrics: PerformanceMetrics
    passes_gate: bool
    rejection_reasons: list[str]


class FleetSelector:
    """Ranks candidate strategies and selects the Top 4 production bots for Alpha's fleet."""

    def __init__(
        self,
        min_net_pf: float = 1.75,
        min_net_rr: float = 1.50,
        max_drawdown_pct: float = 15.0,
    ) -> None:
        self.min_net_pf = min_net_pf
        self.min_net_rr = min_net_rr
        self.max_drawdown_pct = max_drawdown_pct

    def evaluate_and_rank_fleet(
        self,
        metrics_list: list[PerformanceMetrics],
    ) -> tuple[list[StrategyRank], list[PerformanceMetrics]]:
        """
        Evaluates all candidate metrics, applies selection gate, ranks by Net Profit Factor,
        and returns (all_ranks, top_4_selected_fleet).
        """
        ranked_list: list[StrategyRank] = []

        for m in metrics_list:
            rejection_reasons = []
            if m.net_profit_factor < self.min_net_pf:
                rejection_reasons.append(
                    f"Net PF ({m.net_profit_factor}) < {self.min_net_pf}"
                )
            if m.avg_net_rr < self.min_net_rr:
                rejection_reasons.append(
                    f"Net R:R ({m.avg_net_rr}) < {self.min_net_rr}"
                )
            if m.max_drawdown_pct >= self.max_drawdown_pct:
                rejection_reasons.append(
                    f"Max DD ({m.max_drawdown_pct}%) >= {self.max_drawdown_pct}%"
                )
            if not m.survives_friction:
                rejection_reasons.append("Failed survival over statutory fee drag")

            passes_gate = len(rejection_reasons) == 0

            ranked_list.append(
                StrategyRank(
                    rank=0,
                    metrics=m,
                    passes_gate=passes_gate,
                    rejection_reasons=rejection_reasons,
                )
            )

        # Rank all strategies by Net Profit Factor descending, secondary sort by Net Realized PnL %
        ranked_list.sort(
            key=lambda r: (r.metrics.net_profit_factor, r.metrics.net_pnl_pct),
            reverse=True,
        )

        for idx, item in enumerate(ranked_list, start=1):
            item.rank = idx

        # Filter passing strategies for Top 4 fleet (fallback to top Net PF strategies if fewer than 4 pass strict gate)
        passing_strategies = [r.metrics for r in ranked_list if r.passes_gate]

        if len(passing_strategies) >= 4:
            top_4 = passing_strategies[:4]
        else:
            # Fallback to top 4 overall by Net PF if strict criteria produces fewer than 4
            top_4 = [r.metrics for r in ranked_list[:4]]

        return ranked_list, top_4
