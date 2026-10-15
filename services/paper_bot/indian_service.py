"""Stages 6–7: risk/allocation and durable paper execution for Indian Cash Equities."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
from typing import Any

from core.repository.paper_execution_repo import PaperExecutionRepository
from execution.indian_charges import IndianEquityChargeModel
from market.models import MarketQuote
from market.symbols import StockSymbol
from paper.execution.fill_simulator import PaperFillSimulator, PaperOrder


class IndianPaperService:
    """
    Executes paper trades for Indian cash equities.
    Enforces positive whole-share sizing, statutory Indian friction (Delivery/CNC),
    and a guaranteed 1-share minimum order policy for high-priced stocks (e.g. ₹500, ₹1000, ₹3000).
    """

    def __init__(self, repo: PaperExecutionRepository, allocation: Decimal = Decimal("10000")) -> None:
        self.repo = repo
        self.allocation = Decimal(allocation)
        self.charges = IndianEquityChargeModel()
        self.fills = PaperFillSimulator()

    async def handle_signal(self, signal: dict, quote: MarketQuote, now: datetime) -> dict:
        if str(signal.get("mode", "")).upper() != "PAPER":
            return {"stages": [6], "opened": False, "reason": "PAPER_ONLY"}
        symbol = StockSymbol(signal["exchange"], signal["symbol"], signal.get("security_id"))
        if symbol.canonical != quote.symbol.canonical:
            return {"stages": [6], "opened": False, "reason": "QUOTE_SYMBOL_MISMATCH"}

        balance = await self.repo.cash()
        budget = min(balance, self.allocation)
        quantity = int(budget // quote.price)

        charge = None
        debit = Decimal("0")

        # Determine maximum whole shares that fit within the allocation budget
        while quantity > 0:
            charge = self.charges.estimate("BUY", quote.price, quantity, symbol.exchange)
            debit = quote.price * quantity + charge.total
            if debit <= budget:
                break
            quantity -= 1

        # Minimum 1 Whole-Share Rule for Indian Cash Equities:
        # If allocation budget is lower than a single share's price (e.g. stock is ₹2,950 but
        # per-trade allocation budget is ₹1,000 or ₹2,000), allow purchasing at least 1 whole share
        # provided the total cash balance in the account covers the share price + statutory charges.
        if quantity <= 0 and balance > 0:
            one_share_charge = self.charges.estimate("BUY", quote.price, 1, symbol.exchange)
            one_share_debit = quote.price * 1 + one_share_charge.total
            if one_share_debit <= balance:
                quantity = 1
                charge = one_share_charge
                debit = one_share_debit

        if quantity <= 0 or charge is None:
            return {"stages": [6], "opened": False, "reason": "INSUFFICIENT_CASH"}

        order = PaperOrder(f"paper-{signal['id']}", "BUY", "MARKET", quantity)
        fill = self.fills.fill(order, quote, now)
        if not fill:
            return {"stages": [6], "opened": False, "reason": "STALE_OR_UNFILLED_QUOTE"}

        position_id = "pos-" + sha256(str(signal["id"]).encode()).hexdigest()[:16]

        # Record charges and swing-trading exit targets (SL, TP, strategy, timeframe)
        breakdown = {key: str(value) for key, value in charge.as_dict().items()}
        if "stop_loss" in signal:
            breakdown["stop_loss"] = str(signal["stop_loss"])
        if "take_profit" in signal:
            breakdown["take_profit"] = str(signal["take_profit"])
        if "strategy" in signal:
            breakdown["strategy"] = str(signal["strategy"])
        if "timeframe" in signal:
            breakdown["timeframe"] = str(signal["timeframe"])

        opened = await self.repo.open_position_once(
            str(signal["id"]),
            {
                "id": position_id,
                "symbol": symbol.trading_symbol,
                "exchange": symbol.exchange,
                "quantity": quantity,
                "entry_price": fill.price,
                "charges": charge.total,
                "charge_breakdown": json.dumps(breakdown),
                "opened_at": now.isoformat(),
            },
            debit,
        )

        return {
            "stages": [6, 7],
            "opened": opened,
            "reason": None if opened else "SIGNAL_ALREADY_CONSUMED",
            "position_id": position_id,
            "quantity": quantity,
            "charges": charge.as_dict(),
        }

    async def evaluate_open_positions(self, quotes: Any, now: datetime) -> list[dict[str, Any]]:
        """
        Monitors active swing positions across days, weeks, or months.
        Evaluates current quotes against Stop-Loss and Take-Profit price thresholds.
        Executes Delivery (CNC) exits and records net P&L in beta_journal.
        """
        exits = []
        open_positions = await self.repo.get_open_positions()
        for pos in open_positions:
            quote = await quotes.get_latest(f"{pos['exchange']}:{pos['symbol']}")
            if not quote:
                continue

            try:
                meta = json.loads(pos.get("charge_breakdown") or "{}")
            except Exception:
                meta = {}

            sl_str = meta.get("stop_loss")
            tp_str = meta.get("take_profit")
            strategy = meta.get("strategy") or "STE"

            should_exit = False
            exit_reason = None

            if sl_str and quote.price <= Decimal(sl_str):
                should_exit = True
                exit_reason = "STOP_LOSS_HIT"
            elif tp_str and quote.price >= Decimal(tp_str):
                should_exit = True
                exit_reason = "TAKE_PROFIT_HIT"

            if should_exit:
                res = await self.repo.close_position(pos["id"], quote.price, strategy, now)
                exits.append({**res, "position_id": pos["id"], "reason": exit_reason, "exit_price": str(quote.price)})

        return exits
