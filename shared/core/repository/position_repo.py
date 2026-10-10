"""
V2 PositionRepository — open and closed position tracking.
"""

from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite

from core.logging import get_logger
from core.types import BotMode, BotName, ExitReason, Position, PositionStatus

from .base import BaseRepository

logger = get_logger("core.repository.position_repo")


def _dt(s: str | None) -> datetime | None:
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
        id=d["id"],
        bot=BotName(d["bot"]),
        coin=d["coin"],
        pair=d["pair"],
        qty=d["qty"],
        entry_price=d["entry_price"],
        entry_time=_dt(d["entry_time"]),
        mode=BotMode(d["mode"]),
        status=PositionStatus(d.get("status", "OPEN")),
        current_price=d.get("current_price"),
        unrealised_pnl=d.get("unrealised_pnl"),
        stop_loss=d.get("stop_loss"),
        take_profit=d.get("take_profit"),
        signal_id=d.get("signal_id"),
        closed_at=_dt(d.get("closed_at")),
        exit_price=d.get("exit_price"),
        exit_reason=ExitReason(d["exit_reason"]) if d.get("exit_reason") else None,
        exchange_order_id=d.get("exchange_order_id"),
        client_order_id=d.get("client_order_id"),
        filled_qty=d.get("filled_qty"),
        realized_pnl=d.get("realized_pnl"),
        stop_loss_order_id=d.get("stop_loss_order_id"),
        peak_price=d.get("peak_price"),
        trough_price=d.get("trough_price"),
        market_regime=d.get("market_regime"),
    )


class PositionRepository(BaseRepository):
    async def insert(self, position: Position) -> str:
        if position.entry_price <= 0.0:
            logger.error("Repository layer rejected insert: entry_price <= 0 for %s", position.coin)
            raise ValueError(f"Invalid entry_price: {position.entry_price}")

        entry_time_str = (
            position.entry_time.isoformat()
            if hasattr(position.entry_time, "isoformat")
            else str(position.entry_time or datetime.now(timezone.utc).isoformat())
        )
        bot_val = (
            position.bot.value if hasattr(position.bot, "value") else str(position.bot)
        )
        mode_val = (
            position.mode.value
            if hasattr(position.mode, "value")
            else str(position.mode)
        )
        status_val = (
            position.status.value
            if hasattr(position.status, "value")
            else str(position.status)
        )

        sig_id = position.signal_id
        if sig_id:
            try:
                row = await self._fetchone(
                    "SELECT 1 FROM signals WHERE id = ?", (sig_id,)
                )
                if not row:
                    now_str = datetime.now(timezone.utc).isoformat()
                    bot_name = position.bot.value if hasattr(position.bot, "value") else str(position.bot)
                    await self._execute(
                        """
                        INSERT OR IGNORE INTO signals
                        (id, coin, pair, market_state, opportunity_type, priority, risk_level, score, confidence, generated_at, expires_at, source_bot)
                        VALUES (?, ?, ?, 'NORMAL', 'BREAKOUT', 'HIGH', 'LOW', 85, 85, ?, ?, ?)
                        """,
                        (sig_id, position.coin, position.pair, now_str, now_str, bot_name),
                    )
            except Exception as e:
                logger.debug("Failed to ensure signal record for foreign key: %s", e)

        await self._execute(
            """
            INSERT INTO positions
            (id, bot, coin, pair, qty, entry_price, entry_time,
             current_price, unrealised_pnl, stop_loss, take_profit,
             mode, signal_id, status, exchange_order_id, client_order_id, filled_qty,
             stop_loss_order_id, peak_price, trough_price, market_regime)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                position.id,
                bot_val,
                position.coin,
                position.pair,
                position.qty,
                position.entry_price,
                entry_time_str,
                position.current_price,
                position.unrealised_pnl,
                position.stop_loss,
                position.take_profit,
                mode_val,
                sig_id,
                status_val,
                position.exchange_order_id,
                position.client_order_id,
                position.filled_qty,
                position.stop_loss_order_id,
                position.peak_price,
                position.trough_price,
                position.market_regime,
            ),
        )
        return position.id

    async def update_price(
        self,
        position_id: str,
        price: float,
        unrealised_pnl: float,
        peak_price: float | None = None,
        trough_price: float | None = None,
    ) -> None:
        await self._execute(
            """
            UPDATE positions
            SET current_price=?, unrealised_pnl=?,
                peak_price=COALESCE(?, peak_price),
                trough_price=COALESCE(?, trough_price)
            WHERE id=?
            """,
            (price, unrealised_pnl, peak_price, trough_price, position_id),
        )

    async def update_qty(self, position_id: str, new_qty: float) -> None:
        """Update position quantity and filled_qty on partial fills."""
        await self._execute(
            "UPDATE positions SET qty=?, filled_qty=? WHERE id=?",
            (new_qty, new_qty, position_id),
        )

    async def close(
        self,
        position_id: str,
        exit_price: float,
        exit_reason: ExitReason,
        realized_pnl: float | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        reason_val = (
            exit_reason.value if hasattr(exit_reason, "value") else str(exit_reason)
        )
        if realized_pnl is not None:
            await self._execute(
                """
                UPDATE positions
                SET status='CLOSED', exit_price=?, exit_reason=?, closed_at=?, realized_pnl=?
                WHERE id=?
                """,
                (exit_price, reason_val, now, realized_pnl, position_id),
            )
        else:
            await self._execute(
                """
                UPDATE positions
                SET status='CLOSED', exit_price=?, exit_reason=?, closed_at=?
                WHERE id=?
                """,
                (exit_price, reason_val, now, position_id),
            )


    async def get_by_id(self, position_id: str) -> Position | None:
        row = await self._fetchone("SELECT * FROM positions WHERE id=?", (position_id,))
        return _row_to_position(row) if row else None

    get = get_by_id

    async def get_by_signal_id(self, signal_id: str) -> Position | None:
        """Fetch position associated with a specific signal ID."""
        row = await self._fetchone("SELECT * FROM positions WHERE signal_id=?", (signal_id,))
        return _row_to_position(row) if row else None

    async def get_open(
        self,
        bot: BotName | None = None,
        mode: BotMode | str | None = None,
    ) -> list[Position]:
        conditions = ["status != 'CLOSED'"]
        params: list = []
        if bot:
            conditions.append("bot=?")
            params.append(bot.value if hasattr(bot, "value") else str(bot))
        if mode:
            conditions.append("mode=?")
            params.append(mode.value if hasattr(mode, "value") else str(mode))

        where = " AND ".join(conditions)
        rows = await self._fetchall(
            f"SELECT * FROM positions WHERE {where} ORDER BY entry_time DESC",
            tuple(params),
        )
        return [_row_to_position(r) for r in rows]

    async def close_position(
        self,
        position_id: str,
        exit_price: float,
        exit_reason: ExitReason,
        realized_pnl: float | None = None,
    ) -> None:
        await self.close(
            position_id=position_id,
            exit_price=exit_price,
            exit_reason=exit_reason,
            realized_pnl=realized_pnl,
        )

    async def get_open_by_bot(
        self,
        bot: BotName,
        mode: BotMode | str | None = None,
    ) -> list[Position]:
        return await self.get_open(bot=bot, mode=mode)

    async def get_all(
        self,
        limit: int = 50,
        mode: BotMode | str | None = None,
    ) -> list[Position]:
        if mode:
            mode_str = mode.value if hasattr(mode, "value") else str(mode)
            rows = await self._fetchall(
                "SELECT * FROM positions WHERE mode=? ORDER BY entry_time DESC LIMIT ?",
                (mode_str, limit),
            )
        else:
            rows = await self._fetchall(
                "SELECT * FROM positions ORDER BY entry_time DESC LIMIT ?", (limit,)
            )
        return [_row_to_position(r) for r in rows]

    async def get_active_positions(
        self,
        bot: BotName | None = None,
        mode: BotMode | str | None = None,
    ) -> list[Position]:
        """Fetch all non-CLOSED positions (OPEN, PENDING_ENTRY, PENDING_EXIT)."""
        return await self.get_open(bot=bot, mode=mode)

    async def update_status(
        self,
        position_id: str,
        status: PositionStatus | str,
        exit_price: float | None = None,
        exit_reason: ExitReason | str | None = None,
        realized_pnl: float | None = None,
    ) -> None:
        """Update position status and optional closure details."""
        status_val = status.value if hasattr(status, "value") else str(status)
        now = datetime.now(timezone.utc).isoformat()
        reason_val = (
            (exit_reason.value if hasattr(exit_reason, "value") else str(exit_reason))
            if exit_reason
            else None
        )

        if status_val == "CLOSED":
            await self._execute(
                """
                UPDATE positions
                SET status=?, exit_price=?, exit_reason=?, closed_at=?, realized_pnl=COALESCE(?, realized_pnl)
                WHERE id=?
                """,
                (status_val, exit_price, reason_val, now, realized_pnl, position_id),
            )
        else:
            await self._execute(
                "UPDATE positions SET status=? WHERE id=?",
                (status_val, position_id),
            )

    async def get_deployed_capital(
        self,
        bot: BotName,
        mode: BotMode | str | None = None,
    ) -> float:
        conditions = ["status='OPEN'", "bot=?"]
        bot_str = bot.value if hasattr(bot, "value") else str(bot)
        params: list = [bot_str]
        if mode:
            conditions.append("mode=?")
            params.append(mode.value if hasattr(mode, "value") else str(mode))

        where = " AND ".join(conditions)
        row = await self._fetchone(
            f"SELECT COALESCE(SUM(qty * entry_price), 0.0) as total FROM positions WHERE {where}",
            tuple(params),
        )
        return float(row["total"]) if row else 0.0

    async def update_stop_loss_order_id(
        self, position_id: str, stop_loss_order_id: str | None
    ) -> None:
        """Update exchange stop loss order ID for an open position."""
        await self._execute(
            "UPDATE positions SET stop_loss_order_id = ? WHERE id = ?",
            (stop_loss_order_id, position_id),
        )

    async def update_brackets(
        self,
        position_id: str,
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ) -> None:
        """Update stop loss and take profit targets for an open position."""
        await self._execute(
            """
            UPDATE positions
            SET stop_loss = COALESCE(?, stop_loss),
                take_profit = COALESCE(?, take_profit)
            WHERE id = ?
            """,
            (stop_loss, take_profit, position_id),
        )

    async def get_all_deployed_capital(
        self,
        mode: BotMode | str | None = None,
    ) -> dict[str, float]:
        if mode:
            mode_str = mode.value if hasattr(mode, "value") else str(mode)
            rows = await self._fetchall(
                "SELECT bot, COALESCE(SUM(qty * entry_price), 0.0) as total "
                "FROM positions WHERE status='OPEN' AND mode=? GROUP BY bot",
                (mode_str,),
            )
        else:
            rows = await self._fetchall(
                "SELECT bot, COALESCE(SUM(qty * entry_price), 0.0) as total "
                "FROM positions WHERE status='OPEN' GROUP BY bot"
            )
        return {r["bot"]: float(r["total"]) for r in rows}

