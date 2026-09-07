"""Statutory Drag & Tax Ledger Engine for Indian Equities (NSE/BSE)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Any

logger = logging.getLogger(__name__)


@dataclass
class EquityTaxConfig:
    stt_delivery_pct: float = 0.001       # 0.1% STT on Delivery Buy & Sell
    stt_intraday_sell_pct: float = 0.00025# 0.025% STT on Intraday Sell only
    stamp_duty_buy_pct: float = 0.00015   # 0.015% Stamp Duty on Buy
    exchange_turnover_pct: float = 0.0000345 # 0.00345% Exchange Turnover Fee
    brokerage_flat_cap: float = 20.0      # ₹20 flat max per order
    brokerage_pct: float = 0.0003         # 0.03% max
    gst_pct: float = 0.18                 # 18% GST on (Brokerage + Exchange Fee)


class EquityTaxLedger:
    """Calculates exact statutory taxes, exchange fees, and brokerage friction for equity trades."""

    def __init__(self, config: Optional[EquityTaxConfig] = None):
        self.config = config or EquityTaxConfig()

    def calculate_trade_friction(
        self,
        symbol: str,
        entry_price: float,
        exit_price: float,
        quantity: int,
        product: str = "MIS",  # MIS (Intraday) vs CNC (Delivery)
    ) -> Dict[str, Any]:
        """Calculates itemized statutory drag breakdown and net P&L after friction."""
        c = self.config
        buy_turnover = entry_price * quantity
        sell_turnover = exit_price * quantity
        total_turnover = buy_turnover + sell_turnover

        # 1. Brokerage (capped at ₹20 per leg)
        buy_brokerage = min(c.brokerage_flat_cap, buy_turnover * c.brokerage_pct)
        sell_brokerage = min(c.brokerage_flat_cap, sell_turnover * c.brokerage_pct)
        total_brokerage = buy_brokerage + sell_brokerage

        # 2. STT (Securities Transaction Tax)
        if product.upper() == "CNC":
            stt = (buy_turnover + sell_turnover) * c.stt_delivery_pct
        else:
            stt = sell_turnover * c.stt_intraday_sell_pct

        # 3. Stamp Duty (Buy leg only)
        stamp_duty = buy_turnover * c.stamp_duty_buy_pct

        # 4. Exchange Turnover Fee
        exchange_fee = total_turnover * c.exchange_turnover_pct

        # 5. GST (18% on Brokerage + Exchange Fee)
        gst = (total_brokerage + exchange_fee) * c.gst_pct

        # Total Friction
        total_friction = total_brokerage + stt + stamp_duty + exchange_fee + gst

        # Gross vs Net P&L
        gross_pnl = (exit_price - entry_price) * quantity
        net_pnl = gross_pnl - total_friction

        return {
            "symbol": symbol,
            "quantity": quantity,
            "product": product,
            "gross_pnl": round(gross_pnl, 2),
            "net_pnl": round(net_pnl, 2),
            "total_friction": round(total_friction, 2),
            "breakdown": {
                "brokerage": round(total_brokerage, 2),
                "stt": round(stt, 2),
                "stamp_duty": round(stamp_duty, 2),
                "exchange_fee": round(exchange_fee, 2),
                "gst": round(gst, 2),
            },
        }

