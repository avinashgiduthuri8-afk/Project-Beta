"""
PROJECT-ALPHA Master Backtest Execution Runner Script (INR Pairs Edition).

Evaluates 10 candidate algorithmic strategies across 12 mixed-value CoinDCX INR pairs:
  - Mega-Cap High-Value: BTC/INR, ETH/INR, BNB/INR
  - Mid-Cap Medium-Value: SOL/INR, AVAX/INR, LINK/INR
  - Low-Price & Fractional: XRP/INR, ADA/INR, MATIC/INR, DOGE/INR, TRX/INR, SHIB/INR

Applies CoinDCX statutory tax friction (1.0% Sec 194S TDS + 0.20% Fee + 18% GST + Slippage)
and enforces discrete tick and lot rounding (roundp values).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure root directory is on Python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from background.backtest.data_feeder import COINDCX_INR_PAIRS
from background.backtest.engine import BacktestEngine
from background.backtest.fleet_selector import FleetSelector
from background.backtest.friction import FrictionConfig


def main() -> None:
    inr_pairs = list(COINDCX_INR_PAIRS.keys())

    print("=" * 105)
    print(
        " PROJECT-ALPHA V2 - MASTER QUANTITATIVE STRATEGY BACKTESTING ENGINE (COINDCX INR EDITION)"
    )
    print(f" Basket of {len(inr_pairs)} Mixed-Value INR Coins: {', '.join(inr_pairs)}")
    print(
        " Statutory Friction Model: 0.236% Buy Fee | 1.236% Sell (1% TDS + Fee + GST) | 0.10% Slippage Buffer"
    )
    print(
        " Total Round-Trip Drag: 1.572% (INR Spot) with discrete tick and lot rounding (roundp)"
    )
    print("=" * 105)

    # Initial equity: INR 100,000
    initial_capital_inr = 100000.0

    friction_cfg = FrictionConfig(
        exchange_fee_pct=0.20,
        gst_rate_pct=18.0,
        tds_rate_pct=1.00,
        slippage_per_side_pct=0.05,
        is_c2c_pair=False,
    )

    engine = BacktestEngine(
        initial_capital=initial_capital_inr, friction_config=friction_cfg
    )
    selector = FleetSelector(min_net_pf=1.75, min_net_rr=1.50, max_drawdown_pct=15.0)

    print(
        f"\n[INFO] Running backtest across 10 strategies on {len(inr_pairs)} INR pairs (250+ sessions, 15M/1H/4H)...",
        flush=True,
    )
    candidate_metrics = engine.run_all_candidate_strategies(
        pairs=inr_pairs,
        timeframes=["15M", "1H", "4H"],
        sessions=250,
    )

    ranked_list, top_4_fleet = selector.evaluate_and_rank_fleet(candidate_metrics)

    print("\n" + "=" * 105, flush=True)
    print(
        " MASTER STRATEGY EVALUATION & COMPARISON TABLE (AFTER 1.572% INR STATUTORY FRICTION)",
        flush=True,
    )
    print("=" * 105, flush=True)

    header = "| Rank | Strategy Name | Total Trades | Win Rate % | Gross PF | Net PF (After TDS+Fees) | Net Realized PnL (INR) | Net R:R | Max DD (%) | Expectancy / Trade | Fleet Selection |"
    separator = "|:---:|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|"
    print(header, flush=True)
    print(separator, flush=True)

    for item in ranked_list:
        m = item.metrics
        status = (
            "[PROMOTED]"
            if item.passes_gate and m in top_4_fleet
            else ("[ALT FLEET]" if m in top_4_fleet else "[REJECTED]")
        )
        row = (
            f"| **{item.rank}** | **{m.strategy_name}** | {m.total_trades} | {m.win_rate_pct}% | "
            f"{m.gross_profit_factor} | **{m.net_profit_factor}** | +{m.net_pnl_pct}% (INR {m.net_realized_pnl_dollars:,.2f}) | "
            f"1:{m.avg_net_rr} | {m.max_drawdown_pct}% | INR {m.expectancy_per_trade:,.2f} | **{status}** |"
        )
        print(row, flush=True)

    print("\n" + "=" * 105, flush=True)
    print(
        " SELECTED TOP 4 PRODUCTION FLEET STRATEGIES FOR PROJECT-ALPHA (INR PAIRS)",
        flush=True,
    )
    print("=" * 105, flush=True)

    for i, m in enumerate(top_4_fleet, start=1):
        print(f"  {i}. {m.strategy_name.upper()}", flush=True)
        print(
            f"     - Net Profit Factor: {m.net_profit_factor} (Gross: {m.gross_profit_factor})",
            flush=True,
        )
        print(
            f"     - Win Rate: {m.win_rate_pct}% | Net R:R: 1:{m.avg_net_rr} | Expectancy: INR {m.expectancy_per_trade:,.2f}/trade",
            flush=True,
        )
        print(
            f"     - Net Realized PnL: +{m.net_pnl_pct}% (INR {m.net_realized_pnl_dollars:,.2f}) | Max DD: {m.max_drawdown_pct}%\n",
            flush=True,
        )

    print("=" * 105, flush=True)
    print(
        " BACKTEST EXECUTION COMPLETE - ALPHA INR PRODUCTION FLEET SELECTED", flush=True
    )
    print("=" * 105, flush=True)


if __name__ == "__main__":
    main()
