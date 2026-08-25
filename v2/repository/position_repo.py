"""
V2 PositionRepository — open and closed position tracking.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

from v2.core.types import (
    BotMode, BotName, ExitReason, Position, PositionStatus
)
from v2.core.logging import get_logger
from .base import BaseRepository

logger = get_logger("v2.repository.position_repo")


def _dt(s: str | None) -> Optional[datetime]:
    if s is None:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _row_to_position(row: aiosqlite.Row) -> Position:
    d = dict(row)
    return Position(
        id            = d["id"],
        bot           = BotName(d["bot"]),
        coin          = d["coin"],
        pair          = d["pair"],
        qty           = d["qty"],
        entry_price   = d["entry_price"],
        entry_time    = _dt(d["entry_time"]),
        mode          = BotMode(d["mode"]),
        status        = PositionStatus(d.get("status", "OPEN")),
        current_price = d.get("current_price"),
        unrealised_pnl= d.get("unrealised_pnl"),
        stop_loss     = d.get("stop_loss"),
        take_profit   = d.get("take_profit"),
        signal_id     = d.get("signal_id"),
        closed_at     = _dt(d.get("closed_at")),
        exit_price    = d.get("exit_price"),
        exit_reason   = ExitReason(d["exit_reason"]) if d.get("exit_reason") else None,
    )


class PositionRepository(BaseRepository):

    async def insert(self, position: Position) -> str:
        await self._execute(
            """
            INSERT INTO positions
            (id, bot, coin, pair, qty, entry_price, entry_time,
             current_price, unrealised_pnl, stop_loss, take_profit,
             mode, signal_id, status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                position.id,
                position.bot.value,
                position.coin,
                position.pair,
                position.qty,
                position.entry_price,
                position.entry_time.isoformat(),
                position.current_price,
                position.unrealised_pnl,
                position.stop_loss,
                position.take_profit,
                position.mode.value,
                position.signal_id,
                position.status.value,
            ),
        )
        return position.id

    async def update_price(
        self, position_id: str, price: float, unrealised_pnl: float
    ) -> None:
        await self._execute(
            "UPDATE positions SET current_price=?, unrealised_pnl=? WHERE id=?",
            (price, unrealised_pnl, position_id),
        )

    async def close(
        self,
        position_id: str,
        exit_price: float,
        exit_reason: ExitReason,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self._execute(
            """
            UPDATE positions
            SET status='CLOSED', exit_price=?, exit_reason=?, closed_at=?
            WHERE id=?
            """,
            (exit_price, exit_reason.value, now, position_id),
        )

    async def get_by_id(self, position_id: str) -> Optional[Position]:
        row = await self._fetchone(
            "SELECT * FROM positions WHERE id=?", (position_id,)
        )
        return _row_to_position(row) if row else None

    async def get_open(self, bot: Optional[BotName] = None) -> list[Position]:
        if bot:
            rows = await self._fetchall(
                "SELECT * FROM positions WHERE status='OPEN' AND bot=? "
                "ORDER BY entry_time DESC",
                (bot.value,),
            )
        else:
            rows = await self._fetchall(
                "SELECT * FROM positions WHERE status='OPEN' ORDER BY entry_time DESC"
            )
        return [_row_to_position(r) for r in rows]

    async def get_deployed_capital(self, bot: BotName) -> float:
        row = await self._fetchone(
            "SELECT COALESCE(SUM(qty * entry_price), 0.0) as total "
            "FROM positions WHERE status='OPEN' AND bot=?",
            (bot.value,),
        )
        return float(row["total"]) if row else 0.0

    async def get_all_deployed_capital(self) -> dict[str, float]:
        rows = await self._fetchall(
            "SELECT bot, COALESCE(SUM(qty * entry_price), 0.0) as total "
            "FROM positions WHERE status='OPEN' GROUP BY bot"
        )
        return {r["bot"]: float(r["total"]) for r in rows}
