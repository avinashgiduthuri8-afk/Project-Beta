"""
V2 TradeRepository — closed trade history and analytics queries.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import aiosqlite

from v2.core.types import BotMode, BotName, ExitReason, Trade
from v2.core.logging import get_logger
from .base import BaseRepository

logger = get_logger("v2.repository.trade_repo")


def _dt(s: str | None) -> Optional[datetime]:
    if s is None:
        return None
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _row_to_trade(row: aiosqlite.Row) -> Trade:
    d = dict(row)
    return Trade(
        id          = d["id"],
        position_id = d["position_id"],
        bot         = BotName(d["bot"]),
        coin        = d["coin"],
        pair        = d["pair"],
        entry_price = d["entry_price"],
        exit_price  = d["exit_price"],
        qty         = d["qty"],
        pnl         = d["pnl"],
        pnl_pct     = d["pnl_pct"],
        entry_time  = _dt(d["entry_time"]),
        exit_time   = _dt(d["exit_time"]),
        exit_reason = ExitReason(d["exit_reason"]),
        mode        = BotMode(d["mode"]),
        signal_id   = d.get("signal_id"),
    )


class TradeRepository(BaseRepository):

    async def insert(self, trade: Trade) -> str:
        await self._execute(
            """
            INSERT INTO trades
            (id, position_id, bot, coin, pair, entry_price, exit_price,
             qty, pnl, pnl_pct, entry_time, exit_time, exit_reason, mode, signal_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                trade.id,
                trade.position_id,
                trade.bot.value,
                trade.coin,
                trade.pair,
                trade.entry_price,
                trade.exit_price,
                trade.qty,
                trade.pnl,
                trade.pnl_pct,
                trade.entry_time.isoformat(),
                trade.exit_time.isoformat(),
                trade.exit_reason.value,
                trade.mode.value,
                trade.signal_id,
            ),
        )
        return trade.id

    async def get_by_id(self, trade_id: str) -> Optional[Trade]:
        row = await self._fetchone("SELECT * FROM trades WHERE id=?", (trade_id,))
        return _row_to_trade(row) if row else None

    async def get_by_bot(
        self, bot: BotName, limit: int = 100, offset: int = 0
    ) -> list[Trade]:
        rows = await self._fetchall(
            "SELECT * FROM trades WHERE bot=? ORDER BY exit_time DESC LIMIT ? OFFSET ?",
            (bot.value, limit, offset),
        )
        return [_row_to_trade(r) for r in rows]

    async def get_by_coin(self, coin: str, limit: int = 50) -> list[Trade]:
        rows = await self._fetchall(
            "SELECT * FROM trades WHERE coin=? ORDER BY exit_time DESC LIMIT ?",
            (coin, limit),
        )
        return [_row_to_trade(r) for r in rows]

    async def get_since(self, since: datetime) -> list[Trade]:
        rows = await self._fetchall(
            "SELECT * FROM trades WHERE exit_time >= ? ORDER BY exit_time DESC",
            (since.isoformat(),),
        )
        return [_row_to_trade(r) for r in rows]

    async def get_win_rate(
        self,
        bot: Optional[BotName] = None,
        since: Optional[datetime] = None,
    ) -> float:
        """Return fraction of profitable trades (pnl > 0). Returns 0.0 if no trades."""
        conditions = []
        params: list = []
        if bot:
            conditions.append("bot=?")
            params.append(bot.value)
        if since:
            conditions.append("exit_time >= ?")
            params.append(since.isoformat())
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        row = await self._fetchone(
            f"SELECT COUNT(*) as total, "
            f"SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins "
            f"FROM trades {where}",
            tuple(params),
        )
        if not row or not row["total"]:
            return 0.0
        return round(row["wins"] / row["total"], 4)

    async def get_pnl_series(
        self,
        bot: Optional[BotName] = None,
        since: Optional[datetime] = None,
    ) -> list[tuple[datetime, float]]:
        conditions = []
        params: list = []
        if bot:
            conditions.append("bot=?")
            params.append(bot.value)
        if since:
            conditions.append("exit_time >= ?")
            params.append(since.isoformat())
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        rows = await self._fetchall(
            f"SELECT exit_time, pnl FROM trades {where} ORDER BY exit_time ASC",
            tuple(params),
        )
        result = []
        for r in rows:
            dt = _dt(r["exit_time"])
            if dt:
                result.append((dt, float(r["pnl"])))
        return result
