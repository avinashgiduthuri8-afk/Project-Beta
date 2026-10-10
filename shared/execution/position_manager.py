"""
PROJECT-ALPHA Position Manager & State Tracking Engine.

Manages full position lifecycle states: PENDING_ENTRY → OPEN → PENDING_EXIT → CLOSED.
Supports bracket order logic (Stop Loss, Take Profit, Trailing Stop) and deducts statutory
round-trip drag (1.572% total friction: 0.20% exchange fee + 18% GST + 1% Sec 194S TDS + 0.10% slippage)
upon position closure. Persists all transitions directly to SQLite.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.logging import get_logger
from core.repository.position_repo import PositionRepository
from core.repository.trade_repo import TradeRepository
from core.types import BotMode, BotName, ExitReason, Position, PositionStatus, Trade
from execution.trading.precision_rules import (
    normalize_price,
    normalize_qty,
    validate_trade_parameters,
)

logger = get_logger("execution.position_manager")

# Statutory Round-Trip Drag Rate: 1.572% Total Friction
# Breakdown: 0.20% Exchange Fee + 18% GST on Fee (0.036%) + 1.00% Sec 194S TDS + 0.10% Slippage
STATUTORY_ROUND_TRIP_DRAG_RATE = 0.01572


class PositionState(str, Enum):
    PENDING_ENTRY = "PENDING_ENTRY"
    OPEN = "OPEN"
    PENDING_EXIT = "PENDING_EXIT"
    CLOSED = "CLOSED"


class PositionManager:
    """
    Position Lifecycle & State Tracking Manager.
    Handles position state transitions, bracket evaluations (SL, TP, Trailing Stop),
    statutory fee computations, and SQLite database persistence.
    """

    def __init__(
        self,
        position_repo: PositionRepository,
        trade_repo: TradeRepository | None = None,
        bus: EventBus | None = None,
        subaccount_manager: Any | None = None,
    ) -> None:
        self._position_repo = position_repo
        self._trade_repo = trade_repo
        self._bus = bus
        self._subaccount_manager = subaccount_manager
        self._peak_prices: dict[str, float] = {}
        self._trough_prices: dict[str, float] = {}
        self._trailing_stops: dict[str, float] = {}
        self._close_lock = asyncio.Lock()

    def compute_statutory_drag(
        self, entry_price: float, exit_price: float, qty: float
    ) -> float:
        """
        Compute statutory 1.572% round-trip drag friction.
        Friction is calculated on the total traded value (entry notional + exit notional).
        """
        entry_notional = entry_price * qty
        exit_notional = exit_price * qty
        total_traded_value = entry_notional + exit_notional
        # Half of 1.572% applied to round-trip total value, or 1.572% on average position value
        return total_traded_value * (STATUTORY_ROUND_TRIP_DRAG_RATE / 2.0)

    async def register_position(
        self,
        bot: BotName,
        coin: str,
        pair: str,
        entry_price: float,
        qty: float,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        mode: BotMode = BotMode.PAPER,
        signal_id: str | None = None,
        initial_status: PositionState = PositionState.OPEN,
        current_price: float | None = None,
        market_regime: str | None = None,
    ) -> Position:
        """
        Register a new position and persist it to SQLite with strict numeric validation
        and runtime price-jump guard.
        """
        norm_entry = normalize_price(entry_price)
        norm_qty = normalize_qty(qty)
        norm_sl = normalize_price(stop_loss) if stop_loss is not None else None
        norm_tp = normalize_price(take_profit) if take_profit is not None else None

        # Price-jump guard on initial current_price vs entry_price
        if current_price is not None:
            norm_current = normalize_price(current_price)
            ratio = norm_current / norm_entry
            if ratio > 10.0 or ratio < 0.1:
                logger.error(
                    "register_position rejected for %s (%s): abnormal price ratio %.2fx (entry: %.8f, current: %.8f)",
                    coin,
                    pair,
                    ratio,
                    norm_entry,
                    norm_current,
                )
                raise ValueError(
                    f"Abnormal price ratio {ratio:.2f}x between current_price {norm_current} and entry_price {norm_entry}"
                )
        else:
            norm_current = norm_entry

        valid, err_msg = validate_trade_parameters(
            pair=pair,
            price=norm_entry,
            qty=norm_qty,
            stop_loss=norm_sl,
            take_profit=norm_tp,
            is_long=True,
            min_notional=200.0,
        )
        if not valid:
            logger.error("Failed to register position: %s", err_msg)
            raise ValueError(f"Invalid position parameters: {err_msg}")

        pos_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        status_enum = (
            PositionStatus.OPEN
            if initial_status in (PositionState.OPEN, PositionState.PENDING_ENTRY)
            else PositionStatus.CLOSED
        )

        pos = Position(
            id=pos_id,
            bot=bot,
            coin=coin.upper(),
            pair=pair.upper().replace("_", "/"),
            qty=norm_qty,
            entry_price=norm_entry,
            entry_time=now,
            mode=mode,
            status=status_enum,
            current_price=norm_current,
            unrealised_pnl=round((norm_current - norm_entry) * norm_qty, 4),
            stop_loss=norm_sl,
            take_profit=norm_tp,
            signal_id=signal_id,
            peak_price=max(norm_entry, norm_current),
            trough_price=min(norm_entry, norm_current),
            market_regime=market_regime,
        )

        await self._position_repo.insert(pos)

        # Initialize peak price tracker for trailing stop
        self._peak_prices[pos_id] = norm_entry
        self._trough_prices[pos_id] = norm_entry

        if self._bus:
            event_mode = mode.value if hasattr(mode, "value") else str(mode)
            payload = {
                "position_id": pos_id,
                "bot": bot.value if hasattr(bot, "value") else str(bot),
                "coin": pos.coin,
                "pair": pos.pair,
                "entry_price": norm_entry,
                "qty": norm_qty,
                "amount": norm_entry * norm_qty,
                "stop_loss": norm_sl,
                "take_profit": norm_tp,
                "mode": event_mode,
                "service": f"ALPHA_{event_mode.upper()}",
                "status": initial_status.value,
                "opened_at": now.isoformat(),
            }
            await self._bus.publish(EventType.POSITION_OPENED, payload)

        logger.info(
            "[%s] Registered position %s for %s (%s) @ entry=%.8f (Qty: %s, Notional: %.2f)",
            bot.value,
            pos_id,
            pos.coin,
            pos.pair,
            norm_entry,
            norm_qty,
            norm_entry * norm_qty,
        )
        return pos

    open_position = register_position

    async def update_position_state(
        self, position_id: str, new_state: PositionState
    ) -> None:
        """Transition position lifecycle state."""
        status_val = (
            PositionStatus.OPEN
            if new_state != PositionState.CLOSED
            else PositionStatus.CLOSED
        )
        await self._position_repo.update_status(position_id, status_val)
        logger.info(
            "Position %s transitioned to state %s", position_id, new_state.value
        )

    async def update_trailing_stop(
        self, position_id: str, current_price: float, trailing_pct: float = 0.03
    ) -> float | None:
        """
        Update dynamic trailing stop trigger level as peak price moves up.
        """
        pos = await self._position_repo.get_by_id(position_id)
        if not pos or pos.status == PositionStatus.CLOSED:
            return None

        try:
            norm_price = normalize_price(current_price)
        except ValueError:
            return None

        peak = max(self._peak_prices.get(position_id, pos.entry_price), norm_price)
        self._peak_prices[position_id] = peak

        # Trailing stop only activates once peak price reaches profit threshold (>= +3.5%)
        # This protects trades from getting choked by market noise at entry before initial SL
        if pos.entry_price > 0 and peak >= pos.entry_price * 1.035:
            trailing_stop = peak * (1.0 - trailing_pct)
            if pos.stop_loss and trailing_stop < pos.stop_loss:
                trailing_stop = pos.stop_loss
            self._trailing_stops[position_id] = trailing_stop
            return trailing_stop
        else:
            self._trailing_stops[position_id] = pos.stop_loss
            return pos.stop_loss

    async def update_mark_price(
        self,
        pos: Position,
        current_price: float,
        trailing_pct: float = 0.03,
    ) -> float:
        """
        Update mark price, calculate unrealised PnL, update peak and trailing stop,
        and persist directly to PositionRepository.
        Returns unrealised PnL.
        """
        try:
            norm_price = normalize_price(current_price)
        except ValueError as e:
            logger.warning(
                "Invalid mark price for position %s (%s): %s", pos.id, pos.coin, e
            )
            return float(pos.unrealised_pnl or 0.0)

        # Price ratio jump sanity check (protect against 100x cross-quote contamination)
        if pos.entry_price > 0:
            ratio = norm_price / pos.entry_price
            if ratio > 10.0 or ratio < 0.1:
                logger.error(
                    "Abnormal mark price jump for position %s %s (entry: %.8f, current: %.8f, ratio: %.2fx). Skipping mark update to protect P&L.",
                    pos.id,
                    pos.pair,
                    pos.entry_price,
                    norm_price,
                    ratio,
                )
                return float(pos.unrealised_pnl or 0.0)

        # Update peak price for trailing stop evaluation
        peak = max(
            self._peak_prices.get(pos.id, pos.peak_price or pos.entry_price),
            norm_price,
        )
        self._peak_prices[pos.id] = peak
        trough = min(
            self._trough_prices.get(pos.id, pos.trough_price or pos.entry_price),
            norm_price,
        )
        self._trough_prices[pos.id] = trough
        pos.peak_price = peak
        pos.trough_price = trough

        # Update dynamic trailing stop trigger level (only activates after price reaches profit threshold >= +3.5%)
        if pos.entry_price > 0 and peak >= pos.entry_price * 1.035:
            trailing_stop = peak * (1.0 - trailing_pct)
            if pos.stop_loss and trailing_stop < pos.stop_loss:
                trailing_stop = pos.stop_loss
            self._trailing_stops[pos.id] = trailing_stop
        else:
            trailing_stop = pos.stop_loss
            self._trailing_stops[pos.id] = trailing_stop

        # Calculate unrealised PnL: (current_price - entry_price) * qty
        unrealised = round((norm_price - pos.entry_price) * pos.qty, 4)
        pos.current_price = norm_price
        pos.unrealised_pnl = unrealised

        # Persist directly to SQLite
        await self._position_repo.update_price(
            pos.id, norm_price, unrealised, peak, trough
        )

        # Broadcast POSITION_UPDATED on EventBus if present
        if self._bus:
            payload = {
                "position_id": pos.id,
                "bot": pos.bot.value if hasattr(pos.bot, "value") else str(pos.bot),
                "coin": pos.coin,
                "pair": pos.pair,
                "current_price": norm_price,
                "unrealised_pnl": unrealised,
                "peak_price": peak,
                "trough_price": trough,
                "trailing_stop": trailing_stop,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            await self._bus.publish(EventType.POSITION_UPDATED, payload)

        return unrealised

    async def cancel_resting_stop_loss(self, pos: Position) -> None:
        """Cancel resting exchange stop-loss order if present and clear order ID."""
        if getattr(pos, "stop_loss_order_id", None):
            sl_id = pos.stop_loss_order_id
            if self._subaccount_manager:
                try:
                    sub_client = self._subaccount_manager.get_client(pos.bot)
                    await sub_client.cancel_order(sl_id)
                    logger.info(
                        "Cancelled resting stop-loss order %s for position %s (%s)",
                        sl_id,
                        pos.id,
                        pos.coin,
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to cancel resting stop loss order %s for position %s: %s",
                        sl_id,
                        pos.id,
                        e,
                    )
            await self._position_repo.update_stop_loss_order_id(pos.id, None)
            pos.stop_loss_order_id = None

    async def evaluate_brackets(
        self, pair: str, current_price: float
    ) -> list[tuple[Position, ExitReason, float]]:
        """
        Evaluate all active positions for a pair against current price for Stop Loss,
        Take Profit, or Trailing Stop breaches.
        Returns list of (Position, ExitReason, exit_price).
        """
        try:
            norm_price = normalize_price(current_price)
        except ValueError:
            return []

        open_positions = await self._position_repo.get_active_positions()
        triggers: list[tuple[Position, ExitReason, float]] = []

        target_pair = pair.upper().replace("_", "/")

        for pos in open_positions:
            if pos.pair.upper().replace("_", "/") != target_pair:
                continue

            # Update mark price, peak, trailing stop, and persist to SQLite
            await self.update_mark_price(pos, norm_price)

            # 1. Take Profit trigger check
            if pos.take_profit and norm_price >= pos.take_profit:
                await self.cancel_resting_stop_loss(pos)
                triggers.append((pos, ExitReason.TAKE_PROFIT, norm_price))
                continue

            # 2. Stop Loss trigger check
            if pos.stop_loss and norm_price <= pos.stop_loss:
                triggers.append((pos, ExitReason.STOP_LOSS, norm_price))
                continue

            # 3. Trailing Stop trigger check
            trailing_stop = self._trailing_stops.get(pos.id)
            if trailing_stop and norm_price <= trailing_stop:
                await self.cancel_resting_stop_loss(pos)
                triggers.append((pos, ExitReason.STOP_LOSS, norm_price))
                continue

        return triggers

    async def close_position(
        self,
        position_id: str,
        exit_price: float,
        exit_reason: ExitReason = ExitReason.TAKE_PROFIT,
    ) -> tuple[Position | None, Trade | None]:
        """
        Close a position, deduct statutory 1.572% friction, and persist trade record to SQLite.
        Guarantees concurrency serialization and duplicate close protection.
        """
        async with self._close_lock:
            pos = await self._position_repo.get_by_id(position_id)
            if not pos:
                logger.warning(
                    "Attempted to close non-existent position %s",
                    position_id,
                )
                return None, None

            if pos.status == PositionStatus.CLOSED:
                logger.warning(
                    "Attempted to close already CLOSED position %s — returning existing state without modifying DB or creating duplicate trade",
                    position_id,
                )
                existing_trade = None
                if self._trade_repo:
                    trades = await self._trade_repo.get_by_position(position_id)
                    if trades:
                        existing_trade = trades[0]
                return pos, existing_trade

            # Cancel resting stop-loss order if present before finalizing close
            await self.cancel_resting_stop_loss(pos)

            try:
                norm_exit = normalize_price(exit_price)
            except ValueError as e:
                logger.error(
                    "Close position rejected for %s: invalid exit price '%s': %s",
                    position_id,
                    exit_price,
                    e,
                )
                return None, None

            if pos.entry_price <= 0.0 or pos.qty <= 0.0:
                logger.error(
                    "Close position rejected for %s: corrupted entry_price (%.8f) or qty (%.6f)",
                    position_id,
                    pos.entry_price,
                    pos.qty,
                )
                return None, None

            # Price ratio jump protection (never allow corrupted 100x cross-quote jump to record realized P&L)
            ratio = norm_exit / pos.entry_price
            if ratio > 10.0 or ratio < 0.1:
                logger.error(
                    "Position close rejected for %s (%s): extreme price ratio %.2fx (entry: %.8f, exit: %.8f)",
                    pos.id,
                    pos.pair,
                    ratio,
                    pos.entry_price,
                    norm_exit,
                )
                return None, None

            gross_pnl = (norm_exit - pos.entry_price) * pos.qty
            gross_pnl_pct = ((norm_exit - pos.entry_price) / pos.entry_price) * 100.0

            # Deduct statutory 1.572% round-trip drag friction
            fee_drag = self.compute_statutory_drag(pos.entry_price, norm_exit, pos.qty)
            net_realized_pnl = round(gross_pnl - fee_drag, 2)
            net_pnl_pct = round(
                ((net_realized_pnl) / (pos.entry_price * pos.qty)) * 100.0, 2
            )

            now = datetime.now(timezone.utc)

            # Update position record in DB
            await self._position_repo.close_position(
                position_id=pos.id,
                exit_price=norm_exit,
                exit_reason=exit_reason,
                realized_pnl=net_realized_pnl,
            )

            # Keep in-memory position state synchronized
            pos.status = PositionStatus.CLOSED
            pos.exit_price = norm_exit
            pos.exit_reason = exit_reason
            pos.closed_at = now
            pos.realized_pnl = net_realized_pnl

            trade = Trade(
                id=str(uuid.uuid4()),
                position_id=pos.id,
                bot=pos.bot,
                coin=pos.coin,
                pair=pos.pair,
                entry_price=pos.entry_price,
                exit_price=norm_exit,
                qty=pos.qty,
                pnl=net_realized_pnl,
                pnl_pct=net_pnl_pct,
                entry_time=pos.entry_time,
                exit_time=now,
                exit_reason=exit_reason,
                mode=pos.mode,
                signal_id=pos.signal_id,
            )

            if self._trade_repo:
                await self._trade_repo.insert(trade)

            # Clean up tracking dictionaries
            peak_price = max(
                self._peak_prices.get(pos.id, pos.peak_price or pos.entry_price),
                norm_exit,
            )
            trough_price = min(
                self._trough_prices.get(pos.id, pos.trough_price or pos.entry_price),
                norm_exit,
            )
            self._peak_prices.pop(position_id, None)
            self._trough_prices.pop(position_id, None)
            self._trailing_stops.pop(position_id, None)

            if self._bus:
                payload = {
                    "trade_id": trade.id,
                    "position_id": pos.id,
                    "bot": pos.bot.value if hasattr(pos.bot, "value") else str(pos.bot),
                    "coin": pos.coin,
                    "pair": pos.pair,
                    "entry_price": pos.entry_price,
                    "exit_price": norm_exit,
                    "qty": pos.qty,
                    "gross_pnl": round(gross_pnl, 4),
                    "gross_pnl_pct": round(gross_pnl_pct, 2),
                    "statutory_fee_drag": round(fee_drag, 2),
                    "net_pnl": net_realized_pnl,
                    "pnl": net_realized_pnl,
                    "pnl_pct": net_pnl_pct,
                    "exit_reason": (
                        exit_reason.value
                        if hasattr(exit_reason, "value")
                        else str(exit_reason)
                    ),
                    "mode": (
                        pos.mode.value
                        if hasattr(pos.mode, "value")
                        else str(pos.mode)
                    ),
                    "service": f"ALPHA_{(pos.mode.value if hasattr(pos.mode, 'value') else str(pos.mode)).upper()}",
                    "closed_at": now.isoformat(),
                    "opened_at": pos.entry_time.isoformat(),
                    "peak_price": peak_price,
                    "trough_price": trough_price,
                    "tags": [
                        f"strategy:{pos.bot.value if hasattr(pos.bot, 'value') else str(pos.bot)}",
                        f"exit:{exit_reason.value if hasattr(exit_reason, 'value') else str(exit_reason)}",
                        f"pair:{pos.pair}",
                        *(
                            [f"regime:{pos.market_regime}"]
                            if pos.market_regime
                            else []
                        ),
                    ],
                }
                await self._bus.publish(EventType.POSITION_CLOSED, payload)
                await self._bus.publish(EventType.TRADE_CLOSED, payload)

            logger.info(
                "[%s] Position %s CLOSED via %s: entry=%.8f exit=%.8f qty=%.6f gross_pnl=%.4f (%.2f%%) fee_drag=%.2f net_pnl=%.2f (%.2f%%)",
                pos.bot.value,
                pos.id,
                exit_reason.value,
                pos.entry_price,
                norm_exit,
                pos.qty,
                gross_pnl,
                gross_pnl_pct,
                fee_drag,
                net_realized_pnl,
                net_pnl_pct,
            )

            return pos, trade

