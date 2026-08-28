"""Strategy Performance Evaluator & Attribution Engine (Project-Beta)."""

from __future__ import annotations

import sqlite3
from typing import Dict, Any, List
from pathlib import Path


class StrategyEvaluator:
    """Measures setup alpha, score-band win rates, and expectancy from historical trade journal."""

    def __init__(self, db_path: str = "storage/trades.db"):
        self.db_path = Path(db_path)

    def compute_performance_metrics(self) -> Dict[str, Any]:
        if not self.db_path.exists():
            return {"total_trades": 0, "win_rate_pct": 0.0, "expectancy": 0.0}

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT setup_type, realized_pnl, net_pnl, total_score FROM trade_journal WHERE status = 'CLOSED'")
            rows = cursor.fetchall()

        if not rows:
            return {"total_trades": 0, "win_rate_pct": 0.0, "expectancy": 0.0}

        total_trades = len(rows)
        wins = [r for r in rows if r[2] > 0]
        losses = [r for r in rows if r[2] <= 0]

        win_rate = (len(wins) / total_trades) * 100.0 if total_trades > 0 else 0.0
        avg_win = sum(w[2] for w in wins) / len(wins) if wins else 0.0
        avg_loss = abs(sum(l[2] for l in losses) / len(losses)) if losses else 1.0

        profit_factor = (sum(w[2] for w in wins) / max(abs(sum(l[2] for l in losses)), 1.0)) if losses else float(len(wins))
        expectancy = ((win_rate / 100.0) * avg_win) - (((100.0 - win_rate) / 100.0) * avg_loss)

        # Performance by Setup Type
        setup_stats: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            setup = row[0]
            if setup not in setup_stats:
                setup_stats[setup] = {"count": 0, "pnl": 0.0, "wins": 0}
            setup_stats[setup]["count"] += 1
            setup_stats[setup]["pnl"] += row[2]
            if row[2] > 0:
                setup_stats[setup]["wins"] += 1

        for k, v in setup_stats.items():
            v["win_rate"] = round((v["wins"] / v["count"]) * 100.0, 1)
            v["pnl"] = round(v["pnl"], 2)

        return {
            "total_trades": total_trades,
            "win_rate_pct": round(win_rate, 1),
            "profit_factor": round(profit_factor, 2),
            "expectancy_inr": round(expectancy, 2),
            "performance_by_setup": setup_stats,
        }
