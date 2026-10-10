"""
V2 Historical Multi-Timeframe Replay Engine.

Replays historical OHLCV candle streams with strict zero look-ahead bias
(signal calculated on bar N close executes on bar N+1 Open price) and enforces exact
statutory friction drag (1.572% total round-trip drag).
Simulates all 4 production strategies (STE, HDA, VCP, BBS).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import numpy as np

from core.logging import get_logger

logger = get_logger("background.backtest.historical_runner")

# Statutory Round-Trip Drag Rate: 1.572% Total Friction
STATUTORY_ROUND_TRIP_DRAG_RATE = 0.01572


class HistoricalRunner:
    """Historical Multi-Timeframe Replay & Trade Simulation Engine."""

    def __init__(self, initial_capital: float = 100000.0) -> None:
        self.initial_capital = initial_capital

    def compute_statutory_drag(
        self, entry_price: float, exit_price: float, qty: float
    ) -> float:
        """
        Compute statutory 1.572% round-trip drag friction.
        Friction is calculated on total traded value (entry notional + exit notional).
        """
        entry_notional = entry_price * qty
        exit_notional = exit_price * qty
        total_traded_value = entry_notional + exit_notional
        return total_traded_value * (STATUTORY_ROUND_TRIP_DRAG_RATE / 2.0)

    def run_simulation(
        self,
        strategy_name: str,
        pair: str,
        candles: list[dict[str, Any]],
        parameters: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """
        Run trade replay simulation over candle list.
        Each candle dict contains: timestamp, open, high, low, close, volume.

        Strict Zero Look-Ahead Bias Rule:
          - Indicator / signal evaluation uses candle N (up to close price).
          - Orders enter at candle N+1 OPEN price.
        """
        strat_upper = strategy_name.upper()
        params = parameters or {}
        trade_amount = float(params.get("trade_amount", 500.0))
        sl_pct = float(params.get("stop_loss_pct", 0.02))  # Default 2% SL
        tp_pct = float(params.get("take_profit_pct", 0.046))  # Default 4.6% TP
        min_confluence = float(params.get("min_confluence_threshold", 0.0))
        score_multiplier = float(params.get("multiplier", 1.0))

        if len(candles) < 10:
            logger.warning(
                "Insufficient candles (%d) for backtest simulation of %s",
                len(candles),
                strat_upper,
            )
            empty_run = {
                "id": str(uuid.uuid4()),
                "strategy_name": strat_upper,
                "pair": pair,
                "timeframe": str(params.get("timeframe", "5m")),
                "start_time": candles[0]["timestamp"] if candles else "N/A",
                "end_time": candles[-1]["timestamp"] if candles else "N/A",
                "total_trades": 0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "max_drawdown": 0.0,
                "sharpe_ratio": 0.0,
                "cagr": 0.0,
                "parameters": params,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            return empty_run, []

        run_id = str(uuid.uuid4())
        executed_trades: list[dict[str, Any]] = []

        in_position = False
        entry_price = 0.0
        entry_time = ""
        qty = 0.0
        stop_loss = 0.0
        take_profit = 0.0

        # Sort candles by timestamp ascending
        sorted_candles = sorted(candles, key=lambda c: str(c.get("timestamp", "")))

        # Signal generation & execution loop
        for i in range(5, len(sorted_candles) - 1):
            curr_bar = sorted_candles[i]  # Bar N (signal calculation bar)
            next_bar = sorted_candles[i + 1]  # Bar N+1 (execution bar)

            curr_close = float(curr_bar["close"])

            # 1. Manage active open position
            if in_position:
                high = float(curr_bar["high"])
                low = float(curr_bar["low"])

                exit_reason = None
                exit_price = 0.0

                # Check Stop Loss breach
                if low <= stop_loss:
                    exit_reason = "SL_HIT"
                    exit_price = stop_loss
                # Check Take Profit breach
                elif high >= take_profit:
                    exit_reason = "TP_HIT"
                    exit_price = take_profit

                if exit_reason:
                    gross_pnl = (exit_price - entry_price) * qty
                    drag = self.compute_statutory_drag(entry_price, exit_price, qty)
                    net_pnl = round(gross_pnl - drag, 2)

                    executed_trades.append(
                        {
                            "id": str(uuid.uuid4()),
                            "run_id": run_id,
                            "pair": pair,
                            "side": "BUY",
                            "entry_time": entry_time,
                            "exit_time": curr_bar["timestamp"],
                            "entry_price": entry_price,
                            "exit_price": exit_price,
                            "quantity": qty,
                            "gross_pnl": round(gross_pnl, 2),
                            "net_pnl": net_pnl,
                            "statutory_drag": round(drag, 2),
                            "exit_reason": exit_reason,
                        }
                    )

                    in_position = False
                    continue

            # 2. Evaluate entry signal on Bar N (if not in position)
            if not in_position:
                # Calculate simple moving average signal condition
                closes = [float(c["close"]) for c in sorted_candles[i - 5 : i + 1]]
                sma5 = float(np.mean(closes))

                signal_triggered = False
                raw_score = 0.0
                previous_close = closes[-2]
                momentum_pct = (
                    ((curr_close - sma5) / sma5) * 100.0 if sma5 > 0 else 0.0
                )
                previous_volume = float(
                    sorted_candles[i - 1].get("volume", 0.0) or 0.0
                )
                current_volume = float(curr_bar.get("volume", 0.0) or 0.0)
                volume_ratio = (
                    current_volume / previous_volume if previous_volume > 0 else 1.0
                )
                if strat_upper == "STE":
                    signal_triggered = curr_close > sma5 * 1.002
                    raw_score = 82.0 + min(18.0, max(0.0, momentum_pct * 30.0))
                elif strat_upper == "HDA":
                    signal_triggered = curr_close > previous_close and volume_ratio > 1.0
                    raw_score = 80.0 + min(10.0, max(0.0, momentum_pct * 20.0)) + min(
                        10.0, max(0.0, (volume_ratio - 1.0) * 20.0)
                    )
                elif strat_upper == "VCP":
                    volatility = float(np.std(closes))
                    signal_triggered = (
                        volatility < curr_close * 0.01 and curr_close >= previous_close
                    )
                    contraction = max(0.0, 1.0 - (volatility / (curr_close * 0.01)))
                    raw_score = 84.0 + min(16.0, contraction * 16.0)
                elif strat_upper == "BBS":
                    signal_triggered = curr_close > max(closes[:-1])
                    breakout_pct = (
                        ((curr_close - max(closes[:-1])) / max(closes[:-1])) * 100.0
                        if max(closes[:-1]) > 0
                        else 0.0
                    )
                    raw_score = 84.0 + min(16.0, max(0.0, breakout_pct * 30.0))

                # Calibration parameters must materially participate in validation.
                # This deterministic proxy mirrors the scanner's score gate without
                # introducing future bars or network-derived data into the replay.
                effective_score = raw_score * score_multiplier
                if min_confluence > 0:
                    signal_triggered = (
                        signal_triggered and effective_score >= min_confluence
                    )

                if signal_triggered:
                    # ZERO LOOK-AHEAD BIAS: Entry occurs at next_bar OPEN price!
                    entry_price = float(next_bar["open"])
                    entry_time = next_bar["timestamp"]
                    qty = (
                        round(trade_amount / entry_price, 6) if entry_price > 0 else 0.0
                    )

                    stop_loss = entry_price * (1.0 - sl_pct)
                    take_profit = entry_price * (1.0 + tp_pct)
                    in_position = True

        # Calculate summary metrics across executed trades
        total_trades = len(executed_trades)
        if total_trades > 0:
            pnls = [t["net_pnl"] for t in executed_trades]
            wins = sum(1 for p in pnls if p > 0)
            win_rate = round((wins / total_trades) * 100.0, 2)

            gains = sum(p for p in pnls if p > 0)
            losses = abs(sum(p for p in pnls if p < 0))
            profit_factor = (
                round(gains / losses, 2)
                if losses > 0
                else (round(gains, 2) if gains > 0 else 0.0)
            )

            cum_pnl = np.cumsum(pnls)
            peak = np.maximum.accumulate(cum_pnl)
            dd = peak - cum_pnl
            max_drawdown = round(float(np.max(dd)), 2) if len(dd) > 0 else 0.0

            std_pnl = float(np.std(pnls))
            sharpe = (
                round((float(np.mean(pnls)) / std_pnl) * np.sqrt(252), 2)
                if std_pnl > 0
                else 0.0
            )
            cagr = round((sum(pnls) / self.initial_capital) * 100.0, 2)
        else:
            win_rate = 0.0
            profit_factor = 0.0
            max_drawdown = 0.0
            sharpe = 0.0
            cagr = 0.0

        run_summary = {
            "id": run_id,
            "strategy_name": strat_upper,
            "pair": pair,
            "timeframe": str(params.get("timeframe", "5m")),
            "start_time": sorted_candles[0]["timestamp"],
            "end_time": sorted_candles[-1]["timestamp"],
            "total_trades": total_trades,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "max_drawdown": max_drawdown,
            "sharpe_ratio": sharpe,
            "cagr": cagr,
            "parameters": params,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        return run_summary, executed_trades
