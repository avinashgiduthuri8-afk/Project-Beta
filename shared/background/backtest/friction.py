"""Configurable Indian cash-equity execution-cost estimate for backtests."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FrictionConfig:
    product: str = "CNC"
    brokerage_per_order: float = 0.0
    exchange_transaction_pct: float = 0.00297
    sebi_pct: float = 0.0001
    gst_rate_pct: float = 18.0
    delivery_stt_per_side_pct: float = 0.10
    intraday_sell_stt_pct: float = 0.025
    delivery_stamp_buy_pct: float = 0.015
    intraday_stamp_buy_pct: float = 0.003
    slippage_per_side_pct: float = 0.05

    @property
    def intraday(self) -> bool:
        return self.product.upper() in {"MIS", "INTRADAY"}

    @property
    def buy_fee_pct(self) -> float:
        stt = 0.0 if self.intraday else self.delivery_stt_per_side_pct
        stamp = self.intraday_stamp_buy_pct if self.intraday else self.delivery_stamp_buy_pct
        taxable = self.exchange_transaction_pct + self.sebi_pct
        return round(stt + stamp + taxable * (1 + self.gst_rate_pct / 100), 6)

    @property
    def sell_fee_pct(self) -> float:
        stt = self.intraday_sell_stt_pct if self.intraday else self.delivery_stt_per_side_pct
        taxable = self.exchange_transaction_pct + self.sebi_pct
        return round(stt + taxable * (1 + self.gst_rate_pct / 100), 6)

    @property
    def round_trip_fee_pct(self) -> float:
        return round(self.buy_fee_pct + self.sell_fee_pct, 6)

    @property
    def round_trip_slippage_pct(self) -> float:
        return round(self.slippage_per_side_pct * 2, 6)

    @property
    def total_round_trip_drag_pct(self) -> float:
        return round(self.round_trip_fee_pct + self.round_trip_slippage_pct, 6)


class IndianEquityFrictionModel:
    def __init__(self, config: FrictionConfig | None = None) -> None:
        self.config = config or FrictionConfig()

    def get_effective_entry_price(self, raw_entry_price: float) -> float:
        percentage = self.config.buy_fee_pct + self.config.slippage_per_side_pct
        return round(raw_entry_price * (1 + percentage / 100), 4)

    def get_effective_exit_price(self, raw_exit_price: float) -> float:
        percentage = self.config.sell_fee_pct + self.config.slippage_per_side_pct
        return round(raw_exit_price * (1 - percentage / 100), 4)

    def calculate_trade_net_pnl(
        self, entry_price: float, exit_price: float, position_size_qty: float
    ) -> dict[str, float]:
        gross_entry_cost = entry_price * position_size_qty
        gross_exit_proceeds = exit_price * position_size_qty
        effective_entry = self.get_effective_entry_price(entry_price)
        effective_exit = self.get_effective_exit_price(exit_price)
        brokerage = self.config.brokerage_per_order * 2
        gross_pnl = gross_exit_proceeds - gross_entry_cost
        net_pnl = (effective_exit - effective_entry) * position_size_qty - brokerage
        return {
            "gross_entry_cost": round(gross_entry_cost, 2),
            "gross_exit_proceeds": round(gross_exit_proceeds, 2),
            "gross_pnl": round(gross_pnl, 2),
            "gross_pnl_pct": round((gross_pnl / gross_entry_cost * 100) if gross_entry_cost else 0.0, 2),
            "eff_entry_price": effective_entry,
            "eff_exit_price": effective_exit,
            "net_pnl": round(net_pnl, 2),
            "net_pnl_pct": round((net_pnl / gross_entry_cost * 100) if gross_entry_cost else 0.0, 2),
            "total_friction_cost": round(gross_pnl - net_pnl, 2),
        }


# Import compatibility for the cloned backtest engine.
CoinDCXFrictionModel = IndianEquityFrictionModel
