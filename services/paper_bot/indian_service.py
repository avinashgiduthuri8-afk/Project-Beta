"""Stages 6–7: risk/allocation and durable paper execution for Indian Cash Equities."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
from typing import Any
from zoneinfo import ZoneInfo

from core.repository.paper_execution_repo import PaperExecutionRepository
from execution.indian_charges import IndianEquityChargeModel
from market.models import MarketQuote
from market.symbols import StockSymbol
from paper.execution.fill_simulator import PaperFillSimulator, PaperOrder
from services.paper_bot.risk import DailyRiskState, PaperRiskPolicy


class IndianPaperService:
    """
    Executes paper trades for Indian cash equities.
    Enforces positive whole-share sizing, statutory Indian friction (Delivery/CNC),
    and an immutable all-in allocation ceiling.
    """

    def __init__(
        self,
        repo: PaperExecutionRepository,
        allocation: Decimal = Decimal("10000"),
        telegram_client: Any | None = None,
        risk_policy: PaperRiskPolicy | None = None,
        max_open_positions: int = 3,
        max_positions_per_sector: int = 1,
    ) -> None:
        self.repo = repo
        self.allocation = Decimal(allocation)
        self.charges = IndianEquityChargeModel()
        self.fills = PaperFillSimulator()
        self.telegram_client = telegram_client
        self.risk_policy = risk_policy or PaperRiskPolicy()
        self.max_open_positions = int(max_open_positions)
        self.max_positions_per_sector = int(max_positions_per_sector)

    async def handle_signal(self, signal: dict, quote: MarketQuote, now: datetime) -> dict:
        if str(signal.get("mode", "")).upper() != "PAPER":
            return {"stages": [6], "opened": False, "reason": "PAPER_ONLY"}
        symbol = StockSymbol(signal["exchange"], signal["symbol"], signal.get("security_id"))
        if symbol.canonical != quote.symbol.canonical:
            return {"stages": [6], "opened": False, "reason": "QUOTE_SYMBOL_MISMATCH"}
        if await self.repo.is_signal_consumed(str(signal["id"])):
            return {"stages": [6, 7], "opened": False, "reason": "SIGNAL_ALREADY_CONSUMED"}

        trading_date = now.astimezone(ZoneInfo("Asia/Kolkata")).date()
        daily_state = await self.repo.get_daily_risk_state(trading_date, {})
        daily_decision = self.risk_policy.evaluate_daily_state(daily_state)
        if not daily_decision.allowed:
            return {"stages": [6], "opened": False, "reason": daily_decision.code}

        open_positions = await self.repo.get_open_positions()
        if len(open_positions) >= self.max_open_positions:
            return {"stages": [6], "opened": False, "reason": "MAX_OPEN_POSITIONS_REACHED"}
        sector = str(signal.get("sector") or "UNKNOWN").upper()
        same_sector = 0
        for position in open_positions:
            try:
                metadata = json.loads(position.get("charge_breakdown") or "{}")
            except (TypeError, json.JSONDecodeError):
                metadata = {}
            if str(metadata.get("sector") or "UNKNOWN").upper() == sector:
                same_sector += 1
        if same_sector >= self.max_positions_per_sector:
            return {"stages": [6], "opened": False, "reason": "SECTOR_POSITION_LIMIT_REACHED"}

        balance = await self.repo.cash()
        budget = self.allocation
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

        if quantity <= 0 or charge is None:
            return {"stages": [6], "opened": False, "reason": "NOT_AFFORDABLE"}
        if debit > balance:
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
        breakdown["sector"] = sector

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

        if opened and self.telegram_client:
            try:
                import asyncio
                entry_flt = float(fill.price)
                tp_val = float(breakdown.get("take_profit") or (fill.price * Decimal("1.09")))
                sl_val = float(breakdown.get("stop_loss") or (fill.price * Decimal("0.965")))
                asyncio.create_task(
                    self.telegram_client.notify_paper_buy(
                        symbol=symbol.canonical,
                        qty=quantity,
                        entry_price=entry_flt,
                        total_cost=float(debit),
                        target_price=tp_val,
                        sl_price=sl_val,
                        strategy=breakdown.get("strategy") or "SWING",
                    )
                )
            except Exception:
                pass

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
                if self.telegram_client:
                    try:
                        import asyncio
                        entry_flt = float(pos["entry_price"])
                        exit_flt = float(quote.price)
                        qty_int = int(pos["quantity"])
                        gross = (exit_flt - entry_flt) * qty_int
                        net = float(res.get("net_pnl", gross))
                        pct = ((exit_flt - entry_flt) / entry_flt * 100) if entry_flt > 0 else 0.0
                        asyncio.create_task(
                            self.telegram_client.notify_paper_exit(
                                symbol=f"{pos['exchange']}:{pos['symbol']}",
                                qty=qty_int,
                                entry_price=entry_flt,
                                exit_price=exit_flt,
                                gross_pnl=gross,
                                net_pnl=net,
                                pnl_pct=pct,
                                reason=exit_reason,
                            )
                        )
                    except Exception:
                        pass

        return exits

    async def enforce_daily_limits(self, quotes: Any, now: datetime) -> dict[str, Any]:
        """Persist warning/block/stop state and flatten paper positions at the hard limit."""
        trading_date = now.astimezone(ZoneInfo("Asia/Kolkata")).date()
        positions = await self.repo.get_open_positions()
        marks: dict[str, Decimal] = {}
        quote_by_symbol: dict[str, MarketQuote] = {}
        for position in positions:
            canonical = f"{position['exchange']}:{position['symbol']}"
            quote = await quotes.get_latest(canonical)
            if quote and quote.is_fresh(now, self.fills.max_quote_age):
                marks[canonical] = quote.price
                quote_by_symbol[canonical] = quote

        state = await self.repo.get_daily_risk_state(trading_date, marks)
        decision = self.risk_policy.evaluate_daily_state(state)
        state_name = {
            "DAILY_LOSS_LIMIT_REACHED": "EMERGENCY_STOP",
            "NEW_ENTRIES_BLOCKED": "ENTRY_BLOCKED",
            "DAILY_LOSS_WARNING": "WARNING",
        }.get(decision.code, state.state)
        persisted = DailyRiskState(
            state.trading_date, state.realized_pnl, state.unrealized_pnl,
            state.charges, state.consecutive_losses, state_name,
        )
        await self.repo.save_daily_risk_state(persisted, now)

        closed = 0
        if state_name == "EMERGENCY_STOP":
            for position in positions:
                canonical = f"{position['exchange']}:{position['symbol']}"
                quote = quote_by_symbol.get(canonical)
                if quote is None:
                    continue
                try:
                    metadata = json.loads(position.get("charge_breakdown") or "{}")
                except (TypeError, json.JSONDecodeError):
                    metadata = {}
                await self.repo.close_position(
                    position["id"], quote.price, metadata.get("strategy", "RISK_STOP"), now
                )
                closed += 1
        return {"code": decision.code, "loss": decision.loss, "state": state_name, "closed": closed}
