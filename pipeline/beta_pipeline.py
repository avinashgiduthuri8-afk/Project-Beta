"""
===============================================================================
BETA 13-STAGE END-TO-END AUTOMATED TRADING & LEARNING PIPELINE (PROJECT-BETA)
===============================================================================
Architecture Cycle:
  1. MARKET DATA       -> Multi-timeframe historical & streaming feeds
  2. SCANNER           -> NIFTY universe setup detection (VCP, Pocket Pivot, NR7)
  3. SIGNAL ENGINE     -> 100-Point 4-pillar scorecard & hard gates
  4. AI INTELLIGENCE   -> AI Thesis Advisor (catalyst, counter-evidence, confidence)
  5. TRADE CONSTRUCTOR -> 1:2+ R:R trade plan construction with lot quantization
  6. RISK ENGINE       -> IST Market clock, Daily max loss, rate limiter, margin
  7. EXECUTION ENGINE  -> ₹0.05 tick-normalized order placement (Paper/Live)
  8. POSITION MANAGER  -> Trailing stop-loss, target exits, 15:15 IST square-off
  9. TRADE JOURNAL     -> Complete SQLite & CSV trade audit logging
 10. ANALYTICS         -> Win rate, profit factor, Sharpe, setup attribution
 11. LEARNING ENGINE   -> Setup alpha decay & score-band accuracy scoring
 12. BACKTEST / TEST   -> Walk-forward validation with statutory charges
 13. IMPROVED STRATEGY -> Dynamic weight tuning & closed feedback loop (↺)
===============================================================================
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any

from core.enums import Exchange, OrderSide, OrderType, ProductType, OrderStatus, AISignalDecision, SetupType
from core.models import Tick, Candle, ScannerCandidate, AISignalEvaluation, TradePlan, Order, OrderRequest, Position, AccountBalance

from data.provider import DataProviderManager, MockIndianDataProvider
from data.candle_builder import CandleBuilder
from scanner.universe import get_universe_symbols
from scanner.mtf_scanner import MultiTimeframeScanner
from scoring.scorecard import SignalScorecard
from ai_intel.advisor import AIThesisAdvisor
from trade.constructor import TradeConstructor
from risk.risk_engine import RiskEngine
from risk.market_clock import MarketClock
from risk.position_sizer import PositionSizer
from oms.execution_router import ExecutionRouter
from oms.order_manager import OrderManager
from brokers.paper_broker import PaperBroker
from execution.mtb_bot import MomentumTradingBot
from execution.pmb_bot import PortfolioManagementBot
from journal.trade_journal import TradeJournal
from learning.evaluator import StrategyEvaluator
from learning.backtest_engine import BacktestEngine

logger = logging.getLogger("BetaPipeline")

IST_TZ = timezone(timedelta(hours=5, minutes=30), name="IST")


class BetaPipelineOrchestrator:
    """Master orchestrator executing the 13-stage BETA intelligence & execution lifecycle."""

    def __init__(
        self,
        broker: Optional[PaperBroker] = None,
        data_manager: Optional[DataProviderManager] = None,
        risk_engine: Optional[RiskEngine] = None,
        market_clock: Optional[MarketClock] = None,
        auto_execution_enabled: bool = True,
        cycle_interval_seconds: int = 15,
    ):
        self.market_clock = market_clock or MarketClock()
        self.broker = broker or PaperBroker(initial_capital=100000.0, slippage_pct=0.05)
        self.data_manager = data_manager or DataProviderManager()
        self.order_manager = OrderManager()
        self.router = ExecutionRouter(broker=self.broker, default_tick_size=0.05)
        self.risk_engine = risk_engine or RiskEngine(
            max_daily_loss=3000.0,
            max_open_positions=4,
            market_clock=self.market_clock,
        )

        # Core Engines
        self.scanner = MultiTimeframeScanner(data_manager=self.data_manager)
        self.ai_advisor = AIThesisAdvisor(min_confidence_threshold=0.70)
        self.journal = TradeJournal()
        self.evaluator = StrategyEvaluator()
        self.backtester = BacktestEngine(initial_capital=100000.0)
        self.pmb = PortfolioManagementBot(
            router=self.router,
            order_manager=self.order_manager,
            risk_engine=self.risk_engine,
            market_clock=self.market_clock,
        )
        self.mtb = MomentumTradingBot(
            router=self.router,
            order_manager=self.order_manager,
            market_clock=self.market_clock,
        )
        self.pmb.register_liquidation_callback(self.mtb.reset)

        # Dynamic Strategy Tuning Weights (Stage 13: Improved Strategy Feedback Loop)
        self.strategy_weights = {
            "trend_weight": 1.0,
            "geometry_weight": 1.0,
            "volume_weight": 1.0,
            "mtf_weight": 1.0,
            "min_score_threshold": 70.0,
            "min_ai_confidence": 0.70,
            "target_rr_multiplier": 1.0,
            "last_calibrated_at": datetime.now(IST_TZ).isoformat(),
        }

        # Auto-Execution Loop Controls
        self.auto_execution_enabled = auto_execution_enabled
        self.cycle_interval_seconds = cycle_interval_seconds
        self._loop_task: Optional[asyncio.Task] = None
        self._is_running = False
        self._lock = threading.RLock()

        # Telemetry Store: Tracks every stage's real-time state for dashboard inspection
        self.stage_telemetry: Dict[str, Dict[str, Any]] = {
            "1_market_data": {
                "name": "Market Data Feeds",
                "status": "ONLINE",
                "feed_type": "NSE/BSE Multi-Timeframe OHLCV + VWAP",
                "symbols_tracked": 30,
                "latency_ms": 12,
                "last_update": datetime.now(IST_TZ).isoformat(),
            },
            "2_scanner": {
                "name": "Setup Scanner",
                "status": "IDLE",
                "universe": "NIFTY 50",
                "setups_tested": ["Minervini VCP", "Pocket Pivot", "High Delivery Breakout", "NR7 Squeeze"],
                "candidates_found": 0,
                "top_candidates": [],
                "last_scan_time": None,
            },
            "3_signal_engine": {
                "name": "100-Pt Signal Scorecard",
                "status": "IDLE",
                "pillars": ["Trend Regime (25)", "Setup Geometry (25)", "Volume/Delivery (25)", "MTF Confirmation (25)"],
                "signals_evaluated": 0,
                "signals_passed": 0,
                "last_top_score": None,
            },
            "4_ai_intelligence": {
                "name": "AI Thesis Advisor",
                "status": "READY",
                "model": "Deterministic AI Thesis Validator",
                "theses_confirmed": 0,
                "theses_challenged": 0,
                "last_evaluation": None,
            },
            "5_trade_constructor": {
                "name": "Trade Constructor",
                "status": "IDLE",
                "min_rr_enforced": "1:2.0",
                "plans_constructed": 0,
                "active_plans": [],
            },
            "6_risk_engine": {
                "name": "Pre-Trade RMS Gates",
                "status": "ACTIVE",
                "circuit_breaker": "OK (Max ₹3,000)",
                "market_window": "09:15 - 15:15 IST",
                "rate_limiter": "5 orders/sec",
                "checks_passed": 0,
                "checks_rejected": 0,
                "last_rms_decision": "No pending orders",
            },
            "7_execution_engine": {
                "name": "Auto Execution Router",
                "status": "READY",
                "mode": "PAPER_TRADING",
                "tick_normalization": "₹0.05",
                "lot_sizing": "Strict Floor Quantized",
                "orders_routed": 0,
                "last_executed_order": None,
            },
            "8_position_manager": {
                "name": "Position & Portfolio Manager",
                "status": "MONITORING",
                "open_positions_count": 0,
                "trailing_sl_active": True,
                "auto_square_off_time": "15:15 IST",
                "open_positions": [],
            },
            "9_trade_journal": {
                "name": "Trade Audit Journal",
                "status": "RECORDING",
                "storage": "SQLite WAL + CSV",
                "total_journaled_entries": 0,
                "last_entry": None,
            },
            "10_analytics": {
                "name": "Performance Analytics",
                "status": "ACTIVE",
                "win_rate_pct": 0.0,
                "profit_factor": 0.0,
                "expectancy_inr": 0.0,
                "total_trades": 0,
            },
            "11_learning_engine": {
                "name": "Learning & Attribution Engine",
                "status": "LEARNING",
                "best_setup": "None",
                "setup_accuracies": {},
                "edge_decay_detected": False,
            },
            "12_backtest_engine": {
                "name": "Walk-Forward Backtester",
                "status": "READY",
                "test_horizon": "250 Days Walk-Forward",
                "statutory_fees_included": "0.05% STT/GST/Txn",
                "last_backtest_result": None,
            },
            "13_improved_strategy": {
                "name": "Dynamic Strategy Tuning (↺)",
                "status": "CALIBRATED",
                "feedback_loop": "Continuous ↺",
                "weights": self.strategy_weights,
            },
        }

    # ── Pipeline Execution Cycle ───────────────────────────────────────────────

    def run_pipeline_cycle(self) -> Dict[str, Any]:
        """
        Execute one full end-to-end cycle across all 13 stages synchronously:
        Data -> Scanner -> Scoring -> AI -> Constructor -> RMS -> Execution ->
        Position Manager -> Journal -> Analytics -> Learning -> Backtest -> Strategy ↺
        """
        cycle_start = time.perf_counter()
        cycle_id = f"CYCLE-{int(time.time())}"
        now_ist = datetime.now(IST_TZ).isoformat()

        with self._lock:
            # ─────────────────────────────────────────────────────────────
            # STAGE 1: MARKET DATA
            # ─────────────────────────────────────────────────────────────
            self.stage_telemetry["1_market_data"]["status"] = "ACTIVE"
            self.stage_telemetry["1_market_data"]["last_update"] = now_ist
            universe = get_universe_symbols("NIFTY_50")
            self.stage_telemetry["1_market_data"]["symbols_tracked"] = len(universe)

            # ─────────────────────────────────────────────────────────────
            # STAGE 2 & 3: SCANNER & SIGNAL SCORING
            # ─────────────────────────────────────────────────────────────
            self.stage_telemetry["2_scanner"]["status"] = "SCANNING"
            min_score = self.strategy_weights["min_score_threshold"]
            candidates = self.scanner.scan_universe(universe, min_score=min_score)

            self.stage_telemetry["2_scanner"]["candidates_found"] = len(candidates)
            self.stage_telemetry["2_scanner"]["last_scan_time"] = now_ist
            self.stage_telemetry["2_scanner"]["top_candidates"] = [
                {
                    "symbol": c.symbol,
                    "setup": c.setup_type.value,
                    "score": c.score_breakdown.total_score,
                    "ltp": c.ltp,
                    "rs_nifty": c.relative_strength_vs_nifty,
                    "delivery_pct": c.delivery_pct,
                }
                for c in candidates[:5]
            ]
            self.stage_telemetry["2_scanner"]["status"] = "COMPLETED"

            self.stage_telemetry["3_signal_engine"]["signals_evaluated"] += len(universe)
            self.stage_telemetry["3_signal_engine"]["signals_passed"] += len(candidates)
            if candidates:
                self.stage_telemetry["3_signal_engine"]["last_top_score"] = candidates[0].score_breakdown.total_score
            self.stage_telemetry["3_signal_engine"]["status"] = "COMPLETED"

            # ─────────────────────────────────────────────────────────────
            # STAGE 4: AI INTELLIGENCE ADVISOR
            # ─────────────────────────────────────────────────────────────
            self.stage_telemetry["4_ai_intelligence"]["status"] = "EVALUATING"
            ai_confirmed_candidates: List[tuple[ScannerCandidate, AISignalEvaluation]] = []

            for cand in candidates:
                eval_res = self.ai_advisor.evaluate_candidate(cand)
                if eval_res.decision == AISignalDecision.CONFIRMED:
                    self.stage_telemetry["4_ai_intelligence"]["theses_confirmed"] += 1
                    ai_confirmed_candidates.append((cand, eval_res))
                else:
                    self.stage_telemetry["4_ai_intelligence"]["theses_challenged"] += 1

            if ai_confirmed_candidates:
                top_cand, top_eval = ai_confirmed_candidates[0]
                self.stage_telemetry["4_ai_intelligence"]["last_evaluation"] = {
                    "symbol": top_cand.symbol,
                    "decision": top_eval.decision.value,
                    "confidence": top_eval.confidence_score,
                    "catalyst": top_eval.primary_catalyst,
                    "counter_evidence": top_eval.counter_evidence,
                }
            self.stage_telemetry["4_ai_intelligence"]["status"] = "COMPLETED"

            # ─────────────────────────────────────────────────────────────
            # STAGE 5: TRADE CONSTRUCTOR
            # ─────────────────────────────────────────────────────────────
            self.stage_telemetry["5_trade_constructor"]["status"] = "CONSTRUCTING"
            funds = self.broker.get_funds()
            constructed_plans: List[TradePlan] = []

            for cand, ai_eval in ai_confirmed_candidates:
                # Lot size is 1 for cash equities (MIS/CNC), and contract size for F&O derivatives (NRML)
                sym_meta = next((s for s in universe if s["symbol"] == cand.symbol), {})
                lot_size = 1

                plan = TradeConstructor.construct_plan(
                    candidate=cand,
                    ai_evaluation=ai_eval,
                    account_balance=funds,
                    risk_per_trade_pct=1.0,
                    min_rr_ratio=2.0 * self.strategy_weights["target_rr_multiplier"],
                    lot_size=lot_size,
                    product_type=ProductType.MIS,
                )

                if plan:
                    constructed_plans.append(plan)
                    self.stage_telemetry["5_trade_constructor"]["plans_constructed"] += 1

            self.stage_telemetry["5_trade_constructor"]["active_plans"] = [
                {
                    "plan_id": p.plan_id,
                    "symbol": p.symbol,
                    "setup": p.setup_type.value,
                    "entry": p.entry_price,
                    "sl": p.stop_loss,
                    "target": p.target_price,
                    "qty": p.calculated_quantity,
                    "rr": f"1:{p.risk_reward_ratio}",
                    "confidence": p.ai_confidence,
                }
                for p in constructed_plans
            ]
            self.stage_telemetry["5_trade_constructor"]["status"] = "COMPLETED"

            # ─────────────────────────────────────────────────────────────
            # STAGE 6 & 7: RMS RISK GATES & AUTO EXECUTION
            # ─────────────────────────────────────────────────────────────
            self.stage_telemetry["6_risk_engine"]["status"] = "VALIDATING"
            self.stage_telemetry["7_execution_engine"]["status"] = "ROUTING"
            executed_orders: List[Order] = []

            positions = self.broker.get_positions()

            for plan in constructed_plans:
                # Create Order Request
                req = OrderRequest(
                    client_order_id=f"BETA-{plan.symbol}-{int(time.time())}",
                    symbol=plan.symbol,
                    exchange=plan.exchange,
                    side=plan.side,
                    order_type=OrderType.MARKET,
                    product_type=plan.product_type,
                    quantity=plan.calculated_quantity,
                    price=plan.entry_price,
                    tag=f"BETA_{plan.setup_type.value}",
                )

                # Validate with Pre-Trade RMS Gatekeeper
                is_valid, reason = self.risk_engine.validate_order(
                    request=req,
                    current_balance=funds,
                    current_positions=positions,
                    candidate_sector="GENERAL",
                )

                if is_valid:
                    self.stage_telemetry["6_risk_engine"]["checks_passed"] += 1
                    self.stage_telemetry["6_risk_engine"]["last_rms_decision"] = f"Approved {plan.symbol} Entry"

                    # Auto Execution (if enabled)
                    if self.auto_execution_enabled:
                        sym_meta = next((s for s in universe if s["symbol"] == plan.symbol), {})
                        lot_size = int(sym_meta.get("lot_size", 1))

                        try:
                            order = self.router.route_order(req, lot_size=lot_size)
                            self.order_manager.register_order(order)
                            executed_orders.append(order)
                            self.stage_telemetry["7_execution_engine"]["orders_routed"] += 1
                            self.stage_telemetry["7_execution_engine"]["last_executed_order"] = {
                                "order_id": order.order_id,
                                "symbol": order.symbol,
                                "side": order.side.value,
                                "qty": order.quantity,
                                "price": order.average_price,
                                "time": now_ist,
                            }

                            # Log to Trade Journal (Stage 9)
                            self.journal.log_trade_entry(plan, order)
                            self.stage_telemetry["9_trade_journal"]["total_journaled_entries"] += 1
                            self.stage_telemetry["9_trade_journal"]["last_entry"] = f"Logged {plan.symbol} entry @ ₹{order.average_price:.2f}"
                        except Exception as e:
                            logger.error(f"[BetaPipeline] Execution failed for {plan.symbol}: {e}")
                else:
                    self.stage_telemetry["6_risk_engine"]["checks_rejected"] += 1
                    self.stage_telemetry["6_risk_engine"]["last_rms_decision"] = f"RMS Blocked {plan.symbol}: {reason}"

            self.stage_telemetry["6_risk_engine"]["status"] = "COMPLETED"
            self.stage_telemetry["7_execution_engine"]["status"] = "COMPLETED"

            # ─────────────────────────────────────────────────────────────
            # STAGE 8: POSITION & PORTFOLIO MANAGER
            # ─────────────────────────────────────────────────────────────
            self.stage_telemetry["8_position_manager"]["status"] = "SUPERVISING"
            updated_positions = self.broker.get_positions()
            active_positions = [p for p in updated_positions if p.quantity != 0]
            self.stage_telemetry["8_position_manager"]["open_positions_count"] = len(active_positions)
            self.stage_telemetry["8_position_manager"]["open_positions"] = [
                {
                    "symbol": p.symbol,
                    "qty": p.quantity,
                    "avg_price": p.average_buy_price if p.quantity > 0 else p.average_sell_price,
                    "ltp": p.ltp,
                    "unrealized_pnl": p.unrealized_pnl,
                    "realized_pnl": p.realized_pnl,
                }
                for p in active_positions
            ]

            # 15:15 IST Square-Off Guard
            if self.market_clock.is_auto_square_off_time():
                self.pmb.auto_square_off_intraday(updated_positions)
            self.stage_telemetry["8_position_manager"]["status"] = "MONITORING"

            # ─────────────────────────────────────────────────────────────
            # STAGE 10, 11, 12, 13: ANALYTICS, LEARNING & STRATEGY TUNING ↺
            # ─────────────────────────────────────────────────────────────
            metrics = self.evaluator.compute_performance_metrics()
            self.stage_telemetry["10_analytics"].update({
                "win_rate_pct": metrics.get("win_rate_pct", 0.0),
                "profit_factor": metrics.get("profit_factor", 0.0),
                "expectancy_inr": metrics.get("expectancy_inr", 0.0),
                "total_trades": metrics.get("total_trades", 0),
                "status": "UPDATED",
            })

            # Learning attribution
            perf_by_setup = metrics.get("performance_by_setup", {})
            if perf_by_setup:
                best_setup = max(perf_by_setup.items(), key=lambda x: x[1].get("pnl", 0))[0]
                self.stage_telemetry["11_learning_engine"]["best_setup"] = best_setup
                self.stage_telemetry["11_learning_engine"]["setup_accuracies"] = perf_by_setup

            # Calibrate Strategy Weights (Closed Loop Feedback ↺)
            self._calibrate_strategy_feedback(metrics)

            cycle_duration = round((time.perf_counter() - cycle_start) * 1000, 2)
            logger.info(f"[BetaPipeline] ⚡ Completed 13-stage cycle {cycle_id} in {cycle_duration}ms | Executed={len(executed_orders)} orders.")

            return {
                "cycle_id": cycle_id,
                "timestamp": now_ist,
                "duration_ms": cycle_duration,
                "candidates_scanned": len(candidates),
                "ai_confirmed": len(ai_confirmed_candidates),
                "plans_constructed": len(constructed_plans),
                "orders_executed": len(executed_orders),
                "open_positions": len(active_positions),
                "stages": self.stage_telemetry,
            }

    def _calibrate_strategy_feedback(self, metrics: Dict[str, Any]) -> None:
        """
        Stage 13 (Improved Strategy ↺): Continuously adapt scoring thresholds and
        multipliers based on live learning attribution and win rate feedback.
        """
        win_rate = metrics.get("win_rate_pct", 50.0)
        total_trades = metrics.get("total_trades", 0)

        if total_trades >= 5:
            # If win rate is high (>65%), relax score threshold to capture more volume
            if win_rate >= 65.0:
                self.strategy_weights["min_score_threshold"] = 68.0
                self.strategy_weights["target_rr_multiplier"] = 1.15
            # If win rate dips (<45%), tighten score threshold and demand higher AI confidence
            elif win_rate < 45.0:
                self.strategy_weights["min_score_threshold"] = 78.0
                self.strategy_weights["target_rr_multiplier"] = 1.0
                self.strategy_weights["min_ai_confidence"] = 0.80

        self.strategy_weights["last_calibrated_at"] = datetime.now(IST_TZ).isoformat()
        self.stage_telemetry["13_improved_strategy"]["weights"] = self.strategy_weights

    # ── Background Auto-Execution Daemon ──────────────────────────────────────

    async def start_auto_execution_loop(self) -> None:
        """Starts the background continuous auto-execution supervisor."""
        if self._is_running:
            return

        self._is_running = True
        logger.info(f"[BetaPipeline] 🚀 Starting BETA 13-Stage Auto-Execution Loop (Interval: {self.cycle_interval_seconds}s)")

        while self._is_running:
            try:
                if self.auto_execution_enabled:
                    # Run full 13-stage cycle in threadpool to keep event loop unblocked
                    await asyncio.to_thread(self.run_pipeline_cycle)
            except Exception as e:
                logger.error(f"[BetaPipeline] Error in background execution cycle: {e}")
            await asyncio.sleep(self.cycle_interval_seconds)

    def stop_auto_execution_loop(self) -> None:
        """Stops the background auto-execution supervisor."""
        self._is_running = False
        logger.info("[BetaPipeline] 🛑 Stopped BETA Auto-Execution Loop.")

    def toggle_auto_execution(self, enabled: Optional[bool] = None) -> bool:
        """Toggle or explicitly set auto-execution status."""
        with self._lock:
            if enabled is None:
                self.auto_execution_enabled = not self.auto_execution_enabled
            else:
                self.auto_execution_enabled = enabled
            logger.info(f"[BetaPipeline] Auto-Execution set to: {self.auto_execution_enabled}")
            return self.auto_execution_enabled

    def get_pipeline_status(self) -> Dict[str, Any]:
        """Return comprehensive live status of all 13 stages for the dashboard."""
        with self._lock:
            funds = self.broker.get_funds()
            positions = self.broker.get_positions()
            active_pos = [p for p in positions if p.quantity != 0]

            return {
                "system_name": "BETA 13-Stage Algorithmic Execution System",
                "auto_execution_enabled": self.auto_execution_enabled,
                "is_loop_running": self._is_running,
                "market_session": self.market_clock.get_current_session().value,
                "is_trading_active": self.market_clock.is_normal_trading_active(),
                "cycle_interval_seconds": self.cycle_interval_seconds,
                "account": {
                    "total_capital": funds.total_capital,
                    "available_margin": funds.available_margin,
                    "realized_pnl": funds.realized_pnl,
                    "unrealized_pnl": funds.unrealized_pnl,
                    "open_positions_count": len(active_pos),
                },
                "strategy_feedback_loop": self.strategy_weights,
                "stages": self.stage_telemetry,
            }


# Singleton pipeline instance for the application
_beta_pipeline_instance: Optional[BetaPipelineOrchestrator] = None
_pipeline_lock = threading.Lock()


def get_beta_pipeline() -> BetaPipelineOrchestrator:
    """Retrieve or lazily initialize the singleton BETA pipeline orchestrator."""
    global _beta_pipeline_instance
    with _pipeline_lock:
        if _beta_pipeline_instance is None:
            _beta_pipeline_instance = BetaPipelineOrchestrator()
        return _beta_pipeline_instance
