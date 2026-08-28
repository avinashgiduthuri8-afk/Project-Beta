"""Walk-Forward Backtesting Engine for Indian Equities (Project-Beta)."""

from __future__ import annotations

import logging
from typing import List, Dict, Any, Optional
from core.models import Candle, TradePlan
from core.enums import SetupType, OrderSide
from scanner.setups import SetupDetector
from scanner.indicators import Indicators
from scoring.scorecard import SignalScorecard

logger = logging.getLogger(__name__)


class BacktestEngine:
    """Event-driven offline backtester simulating realistic slippage and Indian statutory taxes."""

    def __init__(self, initial_capital: float = 100000.0, slippage_pct: float = 0.05, statutory_fee_pct: float = 0.05):
        self.initial_capital = initial_capital
        self.slippage_pct = slippage_pct
        self.statutory_fee_pct = statutory_fee_pct

    def run_backtest(self, symbol: str, candles: List[Candle], min_score: float = 70.0) -> Dict[str, Any]:
        """Execute walk-forward backtest across historical candles."""
        if len(candles) < 60:
            return {"error": "Insufficient candle data"}

        capital = self.initial_capital
        trades = []
        active_trade: Optional[Dict[str, Any]] = None

        for i in range(50, len(candles)):
            window = candles[:i]
            current_bar = candles[i]

            # 1. Manage open trade
            if active_trade:
                entry = active_trade["entry"]
                sl = active_trade["sl"]
                target = active_trade["target"]
                qty = active_trade["qty"]

                # Check Target Hit
                if current_bar.high >= target:
                    exit_price = target * (1.0 - (self.slippage_pct / 100.0))
                    gross_pnl = (exit_price - entry) * qty
                    fees = (entry + exit_price) * qty * (self.statutory_fee_pct / 100.0)
                    net_pnl = gross_pnl - fees
                    capital += net_pnl
                    trades.append({"symbol": symbol, "result": "TARGET_HIT", "pnl": net_pnl, "entry": entry, "exit": exit_price})
                    active_trade = None

                # Check Stop Loss Hit
                elif current_bar.low <= sl:
                    exit_price = sl * (1.0 - (self.slippage_pct / 100.0))
                    gross_pnl = (exit_price - entry) * qty
                    fees = (entry + exit_price) * qty * (self.statutory_fee_pct / 100.0)
                    net_pnl = gross_pnl - fees
                    capital += net_pnl
                    trades.append({"symbol": symbol, "result": "SL_HIT", "pnl": net_pnl, "entry": entry, "exit": exit_price})
                    active_trade = None

                continue

            # 2. Look for new setup trigger
            is_vcp, meta = SetupDetector.detect_minervini_vcp(window)
            if is_vcp:
                score = SignalScorecard.evaluate(
                    daily_candles=window,
                    h1_candles=[],
                    m15_candles=[],
                    setup_type=SetupType.MINERVINI_VCP,
                    setup_metadata=meta,
                    mansfield_rs=5.0,
                    delivery_pct=55.0,
                    avg_delivery_pct=40.0,
                )

                if score.hard_gates_passed and score.total_score >= min_score:
                    entry = current_bar.close * (1.0 + (self.slippage_pct / 100.0))
                    atr = Indicators.calculate_atr(window, 14)
                    sl = entry - (1.2 * atr)
                    target = entry + (2.0 * (entry - sl))
                    risk_amount = capital * 0.01
                    risk_per_share = entry - sl
                    qty = max(1, int(risk_amount / risk_per_share)) if risk_per_share > 0 else 1

                    active_trade = {
                        "entry": entry,
                        "sl": sl,
                        "target": target,
                        "qty": qty,
                        "bar_idx": i,
                    }

        total_trades = len(trades)
        wins = [t for t in trades if t["pnl"] > 0]
        total_pnl = capital - self.initial_capital
        win_rate = (len(wins) / total_trades) * 100.0 if total_trades > 0 else 0.0

        return {
            "symbol": symbol,
            "initial_capital": self.initial_capital,
            "final_capital": round(capital, 2),
            "net_pnl": round(total_pnl, 2),
            "return_pct": round((total_pnl / self.initial_capital) * 100.0, 2),
            "total_trades": total_trades,
            "win_rate_pct": round(win_rate, 1),
            "trade_log": trades,
        }
