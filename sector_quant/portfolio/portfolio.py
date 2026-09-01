"""Portfolio Management & Mark-to-Market Accounting for Sector Trading."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List, Any, Optional, Set
import pandas as pd
import numpy as np

from sector_quant.data.base import DataHandler
from sector_quant.events.events import (
    MarketEvent,
    SignalEvent,
    SignalType,
    OrderEvent,
    OrderType,
    OrderDirection,
    FillEvent,
)
from sector_quant.events.queue import EventQueue
from sector_quant.portfolio.risk_engine import SectorRiskEngine
from sector_quant.portfolio.position_sizer import PositionSizer
from sector_quant.portfolio.metrics import PerformanceMetrics

logger = logging.getLogger(__name__)


class SectorPortfolio:
    """Manages multi-asset positions, cash, mark-to-market accounting, and sector risk limits."""

    def __init__(
        self,
        bars: DataHandler,
        events_queue: EventQueue,
        initial_capital: float = 100_000.0,
        risk_engine: Optional[SectorRiskEngine] = None,
        position_sizer: Optional[PositionSizer] = None,
        symbol_to_sector: Optional[Dict[str, str]] = None,
    ):
        self.bars = bars
        self.events_queue = events_queue
        self.initial_capital = float(initial_capital)
        self.current_cash = float(initial_capital)
        self.cumulative_commission = 0.0

        self.risk_engine = risk_engine or SectorRiskEngine()
        self.position_sizer = position_sizer or PositionSizer()
        self.symbol_to_sector = symbol_to_sector or {}

        # Symbol tracking
        self.symbol_list = bars.symbol_list
        self.current_positions: Dict[str, int] = {s: 0 for s in self.symbol_list}
        self.current_holdings: Dict[str, float] = {s: 0.0 for s in self.symbol_list}
        self.avg_cost_basis: Dict[str, float] = {s: 0.0 for s in self.symbol_list}

        # Timelines & logs
        self.equity_curve: List[Dict[str, Any]] = []
        self.trade_history: List[Dict[str, Any]] = []
        self.open_trades: Dict[str, List[Dict[str, Any]]] = {s: [] for s in self.symbol_list}
        self.closed_trades: List[Dict[str, Any]] = []

    def update_timeindex(self, event: MarketEvent) -> None:
        """Mark-to-market valuation triggered on every MarketEvent heartbeat."""
        current_dt = event.datetime or datetime.now()
        market_value_total = 0.0
        current_prices: Dict[str, float] = {}

        for sym in self.symbol_list:
            price = self.bars.get_latest_bar_value(sym, "close")
            if price is None or price <= 0:
                price = self.avg_cost_basis.get(sym, 0.0)
            current_prices[sym] = price

            qty = self.current_positions.get(sym, 0)
            mkt_val = qty * price
            self.current_holdings[sym] = mkt_val
            market_value_total += mkt_val

        current_equity = self.current_cash + market_value_total

        self.equity_curve.append({
            "datetime": current_dt,
            "cash": self.current_cash,
            "commission": self.cumulative_commission,
            "market_value": market_value_total,
            "equity": current_equity,
        })

    def update_signal(self, event: SignalEvent) -> None:
        """Translates strategy SignalEvent into risk-checked OrderEvent objects."""
        symbol = event.symbol.upper()
        signal_type = event.signal_type
        current_dt = event.datetime
        sector = event.meta.get("sector", self.symbol_to_sector.get(symbol, "GENERAL"))

        latest_price = self.bars.get_latest_bar_value(symbol, "close")
        if latest_price is None or latest_price <= 0:
            logger.warning(f"Cannot process signal for {symbol}: invalid price")
            return

        current_qty = self.current_positions.get(symbol, 0)
        current_equity = self.get_latest_equity()

        # Handle EXIT signals
        if signal_type == SignalType.EXIT:
            if current_qty > 0:
                # Sell long position
                order = OrderEvent(
                    symbol=symbol,
                    order_type=OrderType.MKT,
                    quantity=abs(current_qty),
                    direction=OrderDirection.SELL,
                    datetime=current_dt,
                    price=latest_price,
                    strategy_id=event.strategy_id,
                    sector=sector,
                    meta=event.meta,
                )
                self.events_queue.put(order)
            elif current_qty < 0:
                # Buy to cover short position
                order = OrderEvent(
                    symbol=symbol,
                    order_type=OrderType.MKT,
                    quantity=abs(current_qty),
                    direction=OrderDirection.BUY,
                    datetime=current_dt,
                    price=latest_price,
                    strategy_id=event.strategy_id,
                    sector=sector,
                    meta=event.meta,
                )
                self.events_queue.put(order)
            return

        # Determine target quantity
        if "pair_symbol" in event.meta and "hedge_ratio" in event.meta:
            pair_sym = event.meta["pair_symbol"]
            pair_price = self.bars.get_latest_bar_value(pair_sym, "close") or latest_price
            beta = float(event.meta["hedge_ratio"])
            leg = event.meta.get("leg", "Y")

            q_y, q_x = self.position_sizer.calculate_pairs_quantities(
                equity=current_equity,
                y_price=latest_price if leg == "Y" else pair_price,
                x_price=pair_price if leg == "Y" else latest_price,
                beta=beta,
            )
            target_qty = q_y if leg == "Y" else q_x
        else:
            target_qty = self.position_sizer.calculate_fixed_fraction_quantity(
                equity=current_equity,
                price=latest_price,
            )

        if target_qty <= 0:
            return

        # Determine order direction
        direction = OrderDirection.BUY if signal_type == SignalType.LONG else OrderDirection.SELL

        # Pre-trade Risk Management Checks
        current_prices = {s: (self.bars.get_latest_bar_value(s, "close") or 0.0) for s in self.symbol_list}
        approved, reason, adjusted_qty = self.risk_engine.validate_order(
            symbol=symbol,
            sector=sector,
            order_direction=direction.value,
            quantity=target_qty,
            price=latest_price,
            current_equity=current_equity,
            current_cash=self.current_cash,
            current_positions=self.current_positions,
            current_prices=current_prices,
            symbol_to_sector=self.symbol_to_sector,
        )

        if not approved:
            logger.info(f"Order rejected by Risk Engine for {symbol}: {reason}")
            return

        if adjusted_qty > 0:
            order = OrderEvent(
                symbol=symbol,
                order_type=OrderType.MKT,
                quantity=adjusted_qty,
                direction=direction,
                datetime=current_dt,
                price=latest_price,
                strategy_id=event.strategy_id,
                sector=sector,
                meta=event.meta,
            )
            self.events_queue.put(order)

    def update_fill(self, event: FillEvent) -> None:
        """Updates portfolio cash, positions, and logs trade execution upon FillEvent."""
        symbol = event.symbol.upper()
        fill_qty = event.quantity
        fill_price = event.fill_price
        direction = event.direction
        commission = event.commission
        cost = fill_qty * fill_price

        self.cumulative_commission += commission

        prev_qty = self.current_positions.get(symbol, 0)

        if direction == OrderDirection.BUY:
            self.current_cash -= (cost + commission)
            new_qty = prev_qty + fill_qty

            # Record open trade or close short
            if prev_qty < 0:
                # Closing short
                close_qty = min(abs(prev_qty), fill_qty)
                entry_cost = self.avg_cost_basis[symbol] * close_qty
                exit_cost = fill_price * close_qty
                pnl = entry_cost - exit_cost - commission  # Short profit = entry - exit
                self.closed_trades.append({
                    "symbol": symbol,
                    "direction": "SHORT",
                    "entry_price": self.avg_cost_basis[symbol],
                    "exit_price": fill_price,
                    "quantity": close_qty,
                    "pnl": pnl,
                    "exit_time": event.timeindex,
                })
            if new_qty != 0:
                self.avg_cost_basis[symbol] = fill_price

            self.current_positions[symbol] = new_qty

        elif direction == OrderDirection.SELL:
            self.current_cash += (cost - commission)
            new_qty = prev_qty - fill_qty

            # Record open trade or close long
            if prev_qty > 0:
                # Closing long
                close_qty = min(prev_qty, fill_qty)
                entry_cost = self.avg_cost_basis[symbol] * close_qty
                exit_cost = fill_price * close_qty
                pnl = exit_cost - entry_cost - commission  # Long profit = exit - entry
                self.closed_trades.append({
                    "symbol": symbol,
                    "direction": "LONG",
                    "entry_price": self.avg_cost_basis[symbol],
                    "exit_price": fill_price,
                    "quantity": close_qty,
                    "pnl": pnl,
                    "exit_time": event.timeindex,
                })
            if new_qty != 0:
                self.avg_cost_basis[symbol] = fill_price

            self.current_positions[symbol] = new_qty

        self.trade_history.append({
            "datetime": event.timeindex,
            "symbol": symbol,
            "direction": direction.value,
            "quantity": fill_qty,
            "price": fill_price,
            "cost": cost,
            "commission": commission,
            "slippage": event.slippage,
            "cash_after": self.current_cash,
        })

    def get_latest_equity(self) -> float:
        if self.equity_curve:
            return float(self.equity_curve[-1]["equity"])
        return self.initial_capital

    def get_equity_dataframe(self) -> pd.DataFrame:
        if not self.equity_curve:
            return pd.DataFrame()
        df = pd.DataFrame(self.equity_curve)
        if "datetime" in df.columns:
            df.set_index("datetime", inplace=True)
        return df

    def get_performance_summary(self) -> Dict[str, Any]:
        eq_df = self.get_equity_dataframe()
        metrics = PerformanceMetrics.calculate_equity_metrics(eq_df)
        trade_metrics = PerformanceMetrics.calculate_trade_metrics(self.closed_trades)
        metrics.update(trade_metrics)
        metrics["cumulative_commission"] = self.cumulative_commission
        return metrics

