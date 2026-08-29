"""
===============================================================================
BETA 13-STAGE END-TO-END PIPELINE LIVE TEST RUN DEMONSTRATOR
===============================================================================
Executes every single stage sequentially with formatted console telemetry.
===============================================================================
"""

import sys
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta

# Set utf-8 stdout encoding for Windows console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.beta_pipeline import BetaPipelineOrchestrator
from brokers.paper_broker import PaperBroker
from risk.market_clock import MarketClock
from core.enums import OrderSide, ProductType



IST_TZ = timezone(timedelta(hours=5, minutes=30), name="IST")

class MockOpenMarketClock(MarketClock):
    """Enforces active trading session for the test run."""
    def is_normal_trading_active(self, dt=None):
        return True


def run_live_test():
    print("=" * 80)
    print("⚡ PROJECT-BETA: 13-STAGE END-TO-END AUTOMATED PIPELINE TEST RUN")
    print(f"🕒 Timestamp: {datetime.now(IST_TZ).strftime('%Y-%m-%d %H:%M:%S IST')}")
    print("=" * 80)

    broker = PaperBroker(initial_capital=500000.0, slippage_pct=0.05)
    clock = MockOpenMarketClock()
    pipeline = BetaPipelineOrchestrator(
        broker=broker,
        market_clock=clock,
        auto_execution_enabled=True,
    )

    print("\n[INITIAL STATUS]")
    status = pipeline.get_pipeline_status()
    print(f"• System: {status['system_name']}")
    print(f"• Auto-Execution: {'ENABLED 🟢' if status['auto_execution_enabled'] else 'DISABLED 🔴'}")
    print(f"• Capital Available: ₹{status['account']['available_margin']:,.2f}")
    print(f"• Total Stages Registered: {len(status['stages'])}")

    print("\n" + "─" * 80)
    print("🚀 EXECUTING COMPLETE 13-STAGE CYCLE...")
    print("─" * 80)

    start_time = time.perf_counter()
    cycle_result = pipeline.run_pipeline_cycle()
    elapsed = (time.perf_counter() - start_time) * 1000

    stages = cycle_result["stages"]

    # Stage 1: Market Data
    s1 = stages["1_market_data"]
    print(f"\n[STAGE 1: {s1['name']}] 🟢 {s1['status']}")
    print(f"  • Feed: {s1['feed_type']}")
    print(f"  • Tracked Symbols: {s1['symbols_tracked']} NSE Equities")
    print(f"  • Latency: {s1['latency_ms']}ms")

    # Stage 2: Scanner
    s2 = stages["2_scanner"]
    print(f"\n[STAGE 2: {s2['name']}] 🟢 {s2['status']}")
    print(f"  • Universe: {s2['universe']}")
    print(f"  • Patterns: {', '.join(s2['setups_tested'])}")
    print(f"  • Candidates Qualified: {s2['candidates_found']}")
    for c in s2["top_candidates"]:
        print(f"    - {c['symbol']:<10} | Setup: {c['setup']:<24} | Score: {c['score']}/100 | LTP: ₹{c['ltp']:<7.2f} | RS: {c['rs_nifty']:+.1f}%")

    # Stage 3: Signal Engine
    s3 = stages["3_signal_engine"]
    print(f"\n[STAGE 3: {s3['name']}] 🟢 {s3['status']}")
    print(f"  • Evaluated: {s3['signals_evaluated']} assets across 4 pillars (Trend, Geometry, Volume, MTF)")
    print(f"  • Passed Hard Gates: {s3['signals_passed']} signals")
    print(f"  • Top Signal Score: {s3['last_top_score']}/100")

    # Stage 4: AI Intelligence
    s4 = stages["4_ai_intelligence"]
    print(f"\n[STAGE 4: {s4['name']}] 🟢 {s4['status']}")
    print(f"  • Layer: {s4['model']}")
    print(f"  • Theses Confirmed: {s4['theses_confirmed']} | Theses Challenged: {s4['theses_challenged']}")
    if s4.get("last_evaluation"):
        ev = s4["last_evaluation"]
        print(f"  • Top Thesis: {ev['symbol']} -> {ev['decision']} (Confidence: {ev['confidence']*100:.0f}%)")
        print(f"    Catalyst: {ev['catalyst']}")

    # Stage 5: Trade Constructor
    s5 = stages["5_trade_constructor"]
    print(f"\n[STAGE 5: {s5['name']}] 🟢 {s5['status']}")
    print(f"  • Min Risk/Reward: {s5['min_rr_enforced']}")
    print(f"  • Plans Constructed: {s5['plans_constructed']}")
    for p in s5["active_plans"]:
        print(f"    - {p['plan_id']}: {p['symbol']} BUY {p['qty']}x @ ₹{p['entry']:.2f} | SL: ₹{p['sl']:.2f} | Target: ₹{p['target']:.2f} (R:R {p['rr']})")

    # Stage 6: Risk Engine
    s6 = stages["6_risk_engine"]
    print(f"\n[STAGE 6: {s6['name']}] 🟢 {s6['status']}")
    print(f"  • Circuit Breaker: {s6['circuit_breaker']}")
    print(f"  • Market Window: {s6['market_window']}")
    print(f"  • RMS Pre-Trade Checks: {s6['checks_passed']} Passed, {s6['checks_rejected']} Rejected")
    print(f"  • Latest Decision: {s6['last_rms_decision']}")

    # Stage 7: Execution Engine
    s7 = stages["7_execution_engine"]
    print(f"\n[STAGE 7: {s7['name']}] 🟢 {s7['status']}")
    print(f"  • Routing Mode: {s7['mode']}")
    print(f"  • Price Normalization: {s7['tick_normalization']} steps | Sizing: {s7['lot_sizing']}")
    print(f"  • Orders Routed to Broker: {s7['orders_routed']}")
    if s7.get("last_executed_order"):
        o = s7["last_executed_order"]
        print(f"  • Executed Order: {o['order_id']} | {o['side']} {o['qty']}x {o['symbol']} @ ₹{o['price']:.2f}")

    # Stage 8: Position Manager
    s8 = stages["8_position_manager"]
    print(f"\n[STAGE 8: {s8['name']}] 🟢 {s8['status']}")
    print(f"  • Active Positions: {s8['open_positions_count']}")
    for pos in s8["open_positions"]:
        print(f"    - {pos['symbol']}: {pos['qty']} shares @ ₹{pos['avg_price']:.2f} (LTP: ₹{pos['ltp']:.2f}) | Unrealized P&L: ₹{pos['unrealized_pnl']:+,.2f}")

    # Stage 9: Trade Journal
    s9 = stages["9_trade_journal"]
    print(f"\n[STAGE 9: {s9['name']}] 🟢 {s9['status']}")
    print(f"  • Storage Audit Trail: {s9['storage']}")
    print(f"  • Total Journaled Entries: {s9['total_journaled_entries']}")
    print(f"  • Latest Entry: {s9['last_entry']}")

    # Stage 10: Analytics
    s10 = stages["10_analytics"]
    print(f"\n[STAGE 10: {s10['name']}] 🟢 {s10['status']}")
    print(f"  • Win Rate: {s10['win_rate_pct']:.1f}%")
    print(f"  • Profit Factor: {s10['profit_factor']:.2f}")
    print(f"  • Trade Expectancy: ₹{s10['expectancy_inr']:,.2f}")

    # Stage 11: Learning Engine
    s11 = stages["11_learning_engine"]
    print(f"\n[STAGE 11: {s11['name']}] 🟢 {s11['status']}")
    print(f"  • Top Alpha Pattern: {s11['best_setup']}")
    print(f"  • Edge Degradation Check: {'DECAY DETECTED ⚠️' if s11['edge_decay_detected'] else 'HEALTHY (ALPHA INTACT) ✅'}")

    # Stage 12: Backtest / Test
    s12 = stages["12_backtest_engine"]
    print(f"\n[STAGE 12: {s12['name']}] 🟢 {s12['status']}")
    print(f"  • Horizon: {s12['test_horizon']}")
    print(f"  • Taxes/Slippage Modeled: {s12['statutory_fees_included']}")

    # Stage 13: Improved Strategy (Closed-Loop Feedback ↺)
    s13 = stages["13_improved_strategy"]
    print(f"\n[STAGE 13: {s13['name']}] 🟢 {s13['status']}")
    print(f"  • Closed-Loop State: {s13['feedback_loop']}")
    print(f"  • Active Score Threshold: {s13['weights']['min_score_threshold']}/100")
    print(f"  • Active R:R Target Multiplier: {s13['weights']['target_rr_multiplier']}x")
    print(f"  • AI Confidence Gate: {s13['weights']['min_ai_confidence']*100:.0f}%")
    print(f"  • Last Calibrated At: {s13['weights']['last_calibrated_at']}")

    print("\n" + "=" * 80)
    print(f"✅ TEST RUN COMPLETED SUCCESSFULLY in {elapsed:.2f}ms")
    print("=" * 80)


if __name__ == "__main__":
    run_live_test()
