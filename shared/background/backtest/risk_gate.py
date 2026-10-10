"""
Stage 06 Risk & Dynamic Position Sizing Gate for Backtesting Suite.

Mandate:
  Net Risk Capital = Account_Equity * 1.0% Max Risk
  Position_Size = Net Risk Capital / (Stop_Loss_Pct + Round_Trip_Fee_Pct + Slippage_Pct)
  Enforces pair lot step precision (round_qty) and minimum trade notional (₹100).
"""

from __future__ import annotations

from dataclasses import dataclass

from .data_feeder import get_pair_spec, round_qty
from .friction import FrictionConfig


@dataclass
class Stage06RiskGate:
    max_risk_pct_per_trade: float = 1.0  # 1.0% max account equity risk per trade
    max_equity_allocation_pct: float = 25.0  # Max 25% account equity in a single trade
    friction_config: FrictionConfig = None

    def __post_init__(self) -> None:
        if self.friction_config is None:
            self.friction_config = FrictionConfig()

    def calculate_position_size(
        self,
        account_equity: float,
        entry_price: float,
        stop_loss_price: float,
        pair: str = "BTC/INR",
    ) -> dict[str, float]:
        """
        Calculates exact risk-adjusted position quantity and amount with discrete lot rounding.
        """
        if account_equity <= 0 or entry_price <= 0:
            return {
                "qty": 0.0,
                "amount": 0.0,
                "risk_capital": 0.0,
                "stop_loss_pct": 0.0,
            }

        spec = get_pair_spec(pair)

        # 1. Calculate raw stop loss percentage
        sl_distance = abs(entry_price - stop_loss_price)
        sl_pct = (sl_distance / entry_price) * 100.0

        # 2. Add total friction drag (fees + GST + TDS + slippage)
        total_risk_per_unit_pct = (
            sl_pct + self.friction_config.total_round_trip_drag_pct
        )

        # 3. Maximum allowed risk capital
        net_risk_capital = account_equity * (self.max_risk_pct_per_trade / 100.0)

        # 4. Position size in quote currency (₹)
        position_amount = net_risk_capital / (total_risk_per_unit_pct / 100.0)

        # 5. Cap position size at max equity allocation limit
        max_allowed_amount = account_equity * (self.max_equity_allocation_pct / 100.0)
        final_amount = min(position_amount, max_allowed_amount)

        # 6. Unrounded position quantity
        raw_qty = final_amount / entry_price

        # 7. Apply discrete lot precision rounding (e.g. 0.00001 for BTC, 1.0 for DOGE, 1000 for SHIB)
        rounded_qty = round_qty(pair, raw_qty)

        # 8. Guard: Ensure quantity meets minimum lot and minimum notional (₹100)
        actual_notional = rounded_qty * entry_price
        if rounded_qty < spec.min_qty or actual_notional < spec.min_notional_inr:
            # If standard risk sizing is slightly below min_notional, check if min_qty is within equity risk
            if spec.min_qty * entry_price <= max_allowed_amount:
                rounded_qty = spec.min_qty
                actual_notional = rounded_qty * entry_price
            else:
                return {
                    "qty": 0.0,
                    "amount": 0.0,
                    "risk_capital": 0.0,
                    "stop_loss_pct": 0.0,
                }

        return {
            "qty": rounded_qty,
            "amount": round(actual_notional, 2),
            "risk_capital": round(net_risk_capital, 2),
            "stop_loss_pct": round(sl_pct, 2),
            "total_risk_per_unit_pct": round(total_risk_per_unit_pct, 2),
        }
