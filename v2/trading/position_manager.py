"""
V2 PositionManager — Lifecycle, Exit Execution, Partial Fills, and Persistence (BETA-CODE-05).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any

from v2.core.types import (
    BotMode,
    BotName,
    ExitReason,
    Position,
    PositionStatus,
    Trade,
)
from v2.core.logging import get_logger
from v2.repository.position_repo import PositionRepository
from v2.repository.trade_repo import TradeRepository

logger = get_logger("v2.trading.position_manager")


class PositionManager:
    """
    Manages position lifecycles, execution fills (including partial fills with average entry price math),
    and DB persistence integration.
    """

    def __init__(
        self,
        position_repo: Optional[PositionRepository] = None,
        trade_repo: Optional[TradeRepository] = None,
    ) -> None:
        self.position_repo = position_repo
        self.trade_repo = trade_repo
        self._positions: Dict[str, Position] = {}

    @property
    def positions(self) -> Dict[str, Position]:
        return self._positions

    async def load_active_positions(self) -> List[Position]:
        """Loads all non-CLOSED positions from repository into memory."""
        if not self.position_repo:
            return list(self._positions.values())

        active = await self.position_repo.get_active()
        for pos in active:
            self._positions[pos.id] = pos
        logger.info(f"Loaded {len(active)} active positions from repository.")
        return active

    async def _publish_event(self, event_type_str: str, position: Position) -> None:
        """Publishes a lifecycle event via the EventBus."""
        try:
            from v2.bus import bus
            from v2.bus.event_types import EventType
            event_type = EventType(event_type_str)
            
            # Use simple dict conversion or a proper mapper. We'll dump important fields.
            payload = {
                "position_id": position.id,
                "symbol": position.symbol,
                "side": position.side,
                "status": position.status.value,
                "qty": position.qty,
                "filled_qty": position.filled_qty,
                "entry_price": position.entry_price,
            }
            await bus.publish(event_type, payload=payload)
        except Exception as e:
            logger.error(f"Failed to publish position event {event_type_str}: {e}")

    async def record_entry_fill(
        self,
        bot: BotName,
        symbol: str,
        side: str,
        requested_qty: float,
        fill_qty: float,
        fill_price: float,
        mode: BotMode,
        internal_order_id: str,
        broker_order_id: str,
        position_id: Optional[str] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        signal_id: Optional[str] = None,
    ) -> Position:
        """
        Records a confirmed entry fill. Creates a position if it doesn't exist.
        Positions are ONLY created from a confirmed FILLED or PARTIALLY_FILLED execution.
        """
        if fill_qty <= 0:
            raise ValueError(f"Cannot create or update position with non-positive fill_qty: {fill_qty}")

        pos = None
        if position_id:
            pos = self._positions.get(position_id)
            if not pos and self.position_repo:
                pos = await self.position_repo.get_by_id(position_id)
                
        # Fallback lookup by broker_order_id or internal_order_id to prevent duplicate creation
        if not pos:
            for p in self._positions.values():
                if (broker_order_id and p.exchange_order_id == broker_order_id) or \
                   (internal_order_id and p.client_order_id == internal_order_id):
                    pos = p
                    break

        # 1. Create Position if it doesn't exist
        is_new = False
        if not pos:
            is_new = True
            pos_id = position_id or str(uuid.uuid4())
            pos = Position(
                id=pos_id,
                bot=bot,
                symbol=symbol,
                side=side.upper(),
                qty=requested_qty,
                filled_qty=0.0,
                entry_price=0.0,
                entry_time=datetime.now(timezone.utc),
                mode=mode,
                status=PositionStatus.PENDING,
                client_order_id=internal_order_id,
                exchange_order_id=broker_order_id,
                stop_loss=stop_loss,
                take_profit=take_profit,
                signal_id=signal_id,
            )
            self._positions[pos.id] = pos

        # Prevent duplicate fill processing by checking order IDs if needed (assume handled before here, but we can't track every fill ID in this model easily without a fills table. We'll assume the caller passes deduplicated fill events).
        # Actually, requirement 9: "Prevent duplicate position creation from repeated broker events."
        # If is_new is False, we already created it. 

        # 2. Update Fill Math
        prev_filled = pos.filled_qty
        prev_price = pos.entry_price
        new_filled = prev_filled + fill_qty

        if new_filled > 0:
            new_entry_price = ((prev_filled * prev_price) + (fill_qty * fill_price)) / new_filled
        else:
            new_entry_price = fill_price

        pos.filled_qty = new_filled
        pos.entry_price = round(new_entry_price, 4)

        # 3. State Transitions
        if pos.filled_qty >= pos.qty and pos.status == PositionStatus.PENDING:
            pos.status = PositionStatus.OPEN

        # 4. Persist and Publish
        if self.position_repo:
            if is_new:
                await self.position_repo.insert(pos)
            else:
                await self.position_repo.update(pos)

        event_str = "position.opened" if is_new else "position.updated"
        await self._publish_event(event_str, pos)

        logger.info(f"Entry Fill -> Position {pos.id} {pos.status.value}: +{fill_qty} @ {fill_price}. Total {pos.filled_qty}/{pos.qty} @ {pos.entry_price}")
        return pos

    async def mark_closing(self, position_id: str, exit_order_id: str, exit_reason: ExitReason) -> Position:
        """
        Transitions position to CLOSING when an exit order is submitted.
        Does NOT close the position.
        """
        pos = self._positions.get(position_id)
        if not pos and self.position_repo:
            pos = await self.position_repo.get_by_id(position_id)
            if pos:
                self._positions[pos.id] = pos

        if not pos:
            raise ValueError(f"Position {position_id} not found.")

        if pos.status == PositionStatus.CLOSED:
            raise ValueError(f"Position {position_id} is already CLOSED.")

        pos.status = PositionStatus.CLOSING
        pos.exit_order_id = exit_order_id
        pos.exit_reason = exit_reason

        if self.position_repo:
            await self.position_repo.update(pos)

        await self._publish_event("position.updated", pos)
        logger.info(f"Position {position_id} marked CLOSING. Exit Order: {exit_order_id}")
        return pos

    async def record_exit_fill(
        self,
        position_id: str,
        fill_qty: float,
        fill_price: float,
        exit_time: Optional[datetime] = None,
    ) -> Tuple[Position, Optional[Trade]]:
        """
        Handles partial or full exit fills.
        Reduces filled_qty. If filled_qty reaches 0, marks as CLOSED and generates Trade.
        """
        pos = self._positions.get(position_id)
        if not pos and self.position_repo:
            pos = await self.position_repo.get_by_id(position_id)
            if pos:
                self._positions[pos.id] = pos

        if not pos:
            raise ValueError(f"Position {position_id} not found.")

        if fill_qty <= 0:
            raise ValueError("Exit fill_qty must be positive.")

        # Reduce inventory
        pos.filled_qty = max(0.0, pos.filled_qty - fill_qty)

        trade = None
        # If inventory is fully exited, transition to CLOSED
        if pos.filled_qty == 0:
            pos.status = PositionStatus.CLOSED
            pos.exit_price = fill_price  # Store final exit price
            pos.closed_at = exit_time or datetime.now(timezone.utc)

            # Generate Trade record
            effective_qty = pos.qty  # For trade PnL, we use the original size
            pnl = round((fill_price - pos.entry_price) * effective_qty, 2)
            # Adjust PnL sign based on side
            if pos.side == "SELL":
                pnl = -pnl

            pnl_pct = round(((fill_price - pos.entry_price) / pos.entry_price) * 100.0, 2)
            if pos.side == "SELL":
                pnl_pct = -pnl_pct

            trade = Trade(
                id=str(uuid.uuid4()),
                position_id=pos.id,
                bot=pos.bot,
                coin=pos.symbol,
                pair=pos.symbol,  # Legacy compat
                entry_price=pos.entry_price,
                exit_price=fill_price,
                qty=effective_qty,
                pnl=pnl,
                pnl_pct=pnl_pct,
                entry_time=pos.entry_time,
                exit_time=pos.closed_at,
                exit_reason=pos.exit_reason or ExitReason.MANUAL,
                mode=pos.mode,
                signal_id=pos.signal_id,
                exchange_order_id=pos.exit_order_id or pos.exchange_order_id,
                client_order_id=pos.client_order_id,
            )
            if self.trade_repo:
                await self.trade_repo.insert(trade)

        if self.position_repo:
            await self.position_repo.update(pos)

        if pos.status == PositionStatus.CLOSED:
            await self._publish_event("position.closed", pos)
            logger.info(f"Position {position_id} CLOSED completely. Trade {trade.id} generated (PnL={trade.pnl}).")
        else:
            await self._publish_event("position.updated", pos)
            logger.info(f"Partial Exit Fill -> Position {position_id} {pos.status.value}: remaining qty={pos.filled_qty}")

        return pos, trade

    def get_position(self, position_id: str) -> Optional[Position]:
        return self._positions.get(position_id)

    def get_open_positions(self, bot: Optional[BotName] = None) -> List[Position]:
        positions = [p for p in self._positions.values() if p.status == PositionStatus.OPEN]
        if bot:
            positions = [p for p in positions if p.bot == bot]
        return positions

    def get_active_positions(self, bot: Optional[BotName] = None) -> List[Position]:
        active_statuses = {PositionStatus.PENDING, PositionStatus.OPEN, PositionStatus.CLOSING}
        positions = [p for p in self._positions.values() if p.status in active_statuses]
        if bot:
            positions = [p for p in positions if p.bot == bot]
        return positions

    async def update_market_prices(self, price_map: Dict[str, float]) -> None:
        """Updates current price and unrealised PnL for active positions."""
        for pos in self.get_active_positions():
            price = price_map.get(pos.symbol)
            if price is not None:
                pos.current_price = price
                pos.unrealised_pnl = round((price - pos.entry_price) * pos.filled_qty, 2)
                if pos.side == "SELL":
                    pos.unrealised_pnl = -pos.unrealised_pnl
                if self.position_repo:
                    await self.position_repo.update_price(pos.id, pos.current_price, pos.unrealised_pnl)

