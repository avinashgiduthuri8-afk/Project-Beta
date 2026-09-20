"""
V2 PositionManager — Lifecycle, Exit Execution, Partial Fills, and Persistence.
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
from v2.trading.stock_broker_client import StockBrokerClient

logger = get_logger("v2.trading.position_manager")


class PositionManager:
    """
    Manages position lifecycles, execution fills (including partial fills with average entry price math),
    real exit order execution via StockBrokerClient, and DB persistence integration.
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

    async def create_pending_position(
        self,
        bot: BotName,
        coin: str,
        pair: str,
        qty: float,
        entry_price: float,
        mode: BotMode,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        signal_id: Optional[str] = None,
        client_order_id: Optional[str] = None,
        exchange_order_id: Optional[str] = None,
    ) -> Position:
        """Creates a position in PENDING status before fill confirmation."""
        pos_id = str(uuid.uuid4())
        pos = Position(
            id=pos_id,
            bot=bot,
            coin=coin,
            pair=pair,
            qty=qty,
            entry_price=entry_price,
            entry_time=datetime.now(timezone.utc),
            mode=mode,
            status=PositionStatus.PENDING,
            stop_loss=stop_loss,
            take_profit=take_profit,
            signal_id=signal_id,
            filled_qty=0.0,
            client_order_id=client_order_id,
            exchange_order_id=exchange_order_id,
        )
        self._positions[pos_id] = pos
        if self.position_repo:
            await self.position_repo.insert(pos)
        logger.info(f"Created PENDING position {pos_id} for {coin} ({pair})")
        return pos

    async def open_position(
        self,
        bot: BotName,
        coin: str,
        pair: str,
        qty: float,
        entry_price: float,
        mode: BotMode,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        signal_id: Optional[str] = None,
        client_order_id: Optional[str] = None,
        exchange_order_id: Optional[str] = None,
    ) -> Position:
        """Directly creates a position in OPEN status upon fill confirmation."""
        pos_id = str(uuid.uuid4())
        pos = Position(
            id=pos_id,
            bot=bot,
            coin=coin,
            pair=pair,
            qty=qty,
            entry_price=entry_price,
            entry_time=datetime.now(timezone.utc),
            mode=mode,
            status=PositionStatus.OPEN,
            stop_loss=stop_loss,
            take_profit=take_profit,
            signal_id=signal_id,
            filled_qty=qty,
            client_order_id=client_order_id,
            exchange_order_id=exchange_order_id,
        )
        self._positions[pos_id] = pos
        if self.position_repo:
            await self.position_repo.insert(pos)
        logger.info(f"Opened position {pos_id} for {coin} @ {entry_price}")
        return pos

    async def on_fill(
        self,
        position_id: str,
        fill_qty: float,
        fill_price: float,
        exchange_order_id: Optional[str] = None,
    ) -> Position:
        """
        Handles execution fill (partial or full).
        Updates filled_qty and computes weighted average entry price:
        new_price = ((prev_filled_qty * prev_price) + (fill_qty * fill_price)) / (prev_filled_qty + fill_qty)
        Transitions status from PENDING to OPEN.
        """
        pos = self._positions.get(position_id)
        if not pos and self.position_repo:
            pos = await self.position_repo.get_by_id(position_id)
            if pos:
                self._positions[pos.id] = pos

        if not pos:
            raise ValueError(f"Position {position_id} not found.")

        prev_filled = pos.filled_qty or 0.0
        prev_price = pos.entry_price or fill_price
        new_filled = prev_filled + fill_qty

        if new_filled > 0:
            new_entry_price = ((prev_filled * prev_price) + (fill_qty * fill_price)) / new_filled
        else:
            new_entry_price = fill_price

        pos.filled_qty = new_filled
        pos.entry_price = round(new_entry_price, 4)
        if exchange_order_id:
            pos.exchange_order_id = exchange_order_id

        if pos.status == PositionStatus.PENDING:
            pos.status = PositionStatus.OPEN

        if self.position_repo:
            await self.position_repo.update(pos)

        logger.info(
            f"Fill recorded for position {position_id}: fill_qty={fill_qty} @ {fill_price}, "
            f"total_filled={pos.filled_qty}, avg_entry_price={pos.entry_price}, status={pos.status.value}"
        )
        return pos

    async def request_exit(
        self,
        position_id: str,
        exit_reason: ExitReason,
        broker_client: StockBrokerClient,
        product: str = "MIS",
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Generates and submits exit order via StockBrokerClient.
        On submission confirmation: sets exit_order_id, transitions status to CLOSING, updates repo.
        Returns (success_flag, broker_response).
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

        qty_to_exit = pos.filled_qty if (pos.filled_qty is not None and pos.filled_qty > 0) else pos.qty

        order_resp = await broker_client.place_order(
            symbol=pos.coin,
            transaction_type="SELL",
            quantity=qty_to_exit,
            price=pos.current_price or pos.entry_price,
            product=product,
        )

        valid, val_reason, order_id = broker_client.validate_execution_response(order_resp)
        if not valid:
            logger.error(f"Exit order rejected for position {position_id}: {val_reason}")
            return False, order_resp

        pos.exit_order_id = order_id
        pos.exit_reason = exit_reason
        pos.status = PositionStatus.CLOSING

        if self.position_repo:
            await self.position_repo.update(pos)

        logger.info(f"Exit order {order_id} submitted for position {position_id}, status -> CLOSING")
        return True, order_resp

    async def on_exit_fill(
        self,
        position_id: str,
        exit_price: float,
        exit_reason: Optional[ExitReason] = None,
        exit_time: Optional[datetime] = None,
    ) -> Tuple[Position, Trade]:
        """
        Confirms exit order execution fill.
        Moves status to CLOSED, records exit_price, exit_reason, closed_at, and creates Trade.
        """
        pos = self._positions.get(position_id)
        if not pos and self.position_repo:
            pos = await self.position_repo.get_by_id(position_id)
            if pos:
                self._positions[pos.id] = pos

        if not pos:
            raise ValueError(f"Position {position_id} not found.")

        pos.status = PositionStatus.CLOSED
        pos.exit_price = exit_price
        pos.closed_at = exit_time or datetime.now(timezone.utc)
        if exit_reason:
            pos.exit_reason = exit_reason
        elif not pos.exit_reason:
            pos.exit_reason = ExitReason.MANUAL

        effective_qty = pos.filled_qty if (pos.filled_qty is not None and pos.filled_qty > 0) else pos.qty
        pnl = round((exit_price - pos.entry_price) * effective_qty, 2)
        pnl_pct = round(((exit_price - pos.entry_price) / pos.entry_price) * 100.0, 2)

        trade = Trade(
            id=str(uuid.uuid4()),
            position_id=pos.id,
            bot=pos.bot,
            coin=pos.coin,
            pair=pos.pair,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            qty=effective_qty,
            pnl=pnl,
            pnl_pct=pnl_pct,
            entry_time=pos.entry_time,
            exit_time=pos.closed_at,
            exit_reason=pos.exit_reason,
            mode=pos.mode,
            signal_id=pos.signal_id,
            exchange_order_id=pos.exit_order_id or pos.exchange_order_id,
            client_order_id=pos.client_order_id,
        )

        if self.position_repo:
            await self.position_repo.update(pos)

        if self.trade_repo:
            await self.trade_repo.insert(trade)

        logger.info(f"Position {position_id} CLOSED. Trade {trade.id} recorded with PnL={pnl} ({pnl_pct}%)")
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
            price = price_map.get(pos.coin) or price_map.get(pos.pair)
            if price is not None:
                pos.current_price = price
                effective_qty = pos.filled_qty if (pos.filled_qty is not None and pos.filled_qty > 0) else pos.qty
                pos.unrealised_pnl = round((price - pos.entry_price) * effective_qty, 2)
                if self.position_repo:
                    await self.position_repo.update_price(pos.id, pos.current_price, pos.unrealised_pnl)

