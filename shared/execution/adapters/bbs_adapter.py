"""
4. BBS (Bollinger Band Squeeze Breakout) Strategy Adapter.

Specification:
  - Bollinger Bands (20,2) squeeze inside Keltner Channels (20, 1.5 ATR)
  - Base SL: 8.0% (Tightened: 5.0%), Base TP: 25.0%
  - Minimum Net R:R >= 2.00 after statutory friction deductions
"""

from __future__ import annotations

from typing import Any

from core.types import BotName
from execution.trading.precision_rules import round_price, round_qty, round_qty_up

from .base import BaseBotAdapter


class BBSAdapter(BaseBotAdapter):
    """Execution adapter for Bollinger Band Squeeze Breakout bot."""

    def __init__(self) -> None:
        super().__init__(BotName.BBS)
        self.base_sl_pct = 4.5
        self.tightened_sl_pct = 3.5
        self.take_profit_pct = 6.2

    def calculate_order(
        self,
        coin: str,
        pair: str,
        approved_amount: float,
        current_price: float,
        ai_adjustments: dict,
    ) -> dict[str, Any]:
        tighten = ai_adjustments.get("tighten_stop", False)
        sl_pct = self.tightened_sl_pct if tighten else self.base_sl_pct

        # Dynamic TP Classification:
        # - Super Elite (Score >= 95 or super_conviction): Target 25.0%+ fast runner
        # - Elite (Score 90-94): Target 14.0% (10.0% - 15.0% MTF swing)
        # - Standard (Score < 90): Target base TP (6.2%)
        score = float(
            ai_adjustments.get("score") or ai_adjustments.get("confluence_score") or 0.0
        )
        if score >= 95.0 or ai_adjustments.get("super_elite", False):
            tp_pct = 25.0
        elif score >= 90.0 or ai_adjustments.get("high_conviction", False) or ai_adjustments.get("elite", False):
            tp_pct = 14.0
        else:
            tp_pct = self.take_profit_pct

        rounded_entry = round_price(pair, current_price)
        raw_sl = rounded_entry * (1.0 - sl_pct / 100.0)
        raw_tp = rounded_entry * (1.0 + tp_pct / 100.0)

        rounded_sl = round_price(pair, raw_sl)
        rounded_tp = round_price(pair, raw_tp)

        usdt_inr_rate = float(ai_adjustments.get("usdt_inr_rate", 91.50))
        is_usdt = pair.endswith("USDT")
        
        target_amount = float(approved_amount)
        if is_usdt and target_amount >= 50.0:
            target_amount = target_amount / usdt_inr_rate

        min_notional = (200.0 / usdt_inr_rate) if is_usdt else 200.0
        target_amount = max(min_notional, target_amount)
        
        raw_qty = target_amount / rounded_entry if rounded_entry > 0 else 0.0
        rounded_qty = round_qty(pair, raw_qty)
        if rounded_entry * rounded_qty < min_notional and rounded_entry > 0:
            rounded_qty = round_qty_up(pair, min_notional / rounded_entry)

        return {
            "bot": self.bot_name,
            "coin": coin,
            "pair": pair,
            "entry_price": rounded_entry,
            "qty": rounded_qty,
            "amount": round(rounded_entry * rounded_qty, 2),
            "stop_loss": rounded_sl,
            "take_profit": rounded_tp,
            "strategy": "Bollinger Band Squeeze Breakout",
            "sl_pct": sl_pct,
            "tp_pct": tp_pct,
            "net_rr_target": 1.50,
        }
