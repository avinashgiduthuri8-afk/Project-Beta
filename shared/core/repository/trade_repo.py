"""
V2 TradeRepository — closed trade history and analytics queries.
"""

from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite

from core.logging import get_logger
from core.types import BotMode, BotName, ExitReason, Trade

from .base import BaseRepository

logger = get_logger("core.repository.trade_repo")


def _dt(s: str | None) -> datetime | None:
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
        id=d["id"],
        position_id=d["position_id"],
        bot=BotName(d["bot"]),
        coin=d["coin"],
        pair=d["pair"],
        entry_price=d["entry_price"],
        exit_price=d["exit_price"],
        qty=d["qty"],
        pnl=d["pnl"],
        pnl_pct=d["pnl_pct"],
        entry_time=_dt(d["entry_time"]),
        exit_time=_dt(d["exit_time"]),
        exit_reason=ExitReason(d["exit_reason"]),
        mode=BotMode(d["mode"]),
        signal_id=d.get("signal_id"),
        exchange_order_id=d.get("exchange_order_id"),
        client_order_id=d.get("client_order_id"),
    )


class TradeRepository(BaseRepository):
    async def insert(self, trade: Trade) -> str:
        # Strict idempotency: ensure exactly ONE trade per position
        if trade.position_id:
            try:
                existing = await self._fetchone(
                    "SELECT id FROM trades WHERE position_id = ?", (trade.position_id,)
                )
                if existing:
                    existing_id = (
                        existing["id"]
                        if isinstance(existing, dict) or hasattr(existing, "__getitem__")
                        else existing[0]
                    )
                    logger.warning(
                        "Trade already exists for position %s (trade_id=%s). Skipping duplicate insert.",
                        trade.position_id,
                        existing_id,
                    )
                    return str(existing_id)
            except Exception as chk_err:
                logger.error(
                    "Error checking existing trade for position %s: %s",
                    trade.position_id,
                    chk_err,
                )

        entry_time_str = (
            trade.entry_time.isoformat()
            if hasattr(trade.entry_time, "isoformat")
            else str(trade.entry_time or datetime.now(timezone.utc).isoformat())
        )
        exit_time_str = (
            trade.exit_time.isoformat()
            if hasattr(trade.exit_time, "isoformat")
            else str(trade.exit_time or datetime.now(timezone.utc).isoformat())
        )
        exit_reason_val = (
            trade.exit_reason.value
            if hasattr(trade.exit_reason, "value")
            else str(trade.exit_reason)
        )
        mode_val = trade.mode.value if hasattr(trade.mode, "value") else str(trade.mode)
        bot_val = trade.bot.value if hasattr(trade.bot, "value") else str(trade.bot)

        sig_id = trade.signal_id
        if sig_id:
            try:
                row = await self._fetchone(
                    "SELECT 1 FROM signals WHERE id = ?", (sig_id,)
                )
                if not row:
                    sig_id = None
            except Exception:
                sig_id = None

        await self._execute(
            """
            INSERT INTO trades
            (id, position_id, bot, coin, pair, entry_price, exit_price,
             qty, pnl, pnl_pct, entry_time, exit_time, exit_reason, mode, signal_id,
             exchange_order_id, client_order_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                trade.id,
                trade.position_id,
                bot_val,
                trade.coin,
                trade.pair,
                trade.entry_price,
                trade.exit_price,
                trade.qty,
                trade.pnl,
                trade.pnl_pct,
                entry_time_str,
                exit_time_str,
                exit_reason_val,
                mode_val,
                sig_id,
                trade.exchange_order_id,
                trade.client_order_id,
            ),
        )
        return trade.id

    async def get_by_id(self, trade_id: str) -> Trade | None:
        row = await self._fetchone("SELECT * FROM trades WHERE id=?", (trade_id,))
        return _row_to_trade(row) if row else None

    async def get_by_bot(
        self,
        bot: BotName,
        limit: int = 100,
        offset: int = 0,
        mode: BotMode | str | None = None,
    ) -> list[Trade]:
        conditions = ["bot=?"]
        bot_str = bot.value if hasattr(bot, "value") else str(bot)
        params: list = [bot_str]
        if mode:
            conditions.append("mode=?")
            params.append(mode.value if hasattr(mode, "value") else str(mode))

        where = " AND ".join(conditions)
        params.extend([limit, offset])
        rows = await self._fetchall(
            f"SELECT * FROM trades WHERE {where} ORDER BY exit_time DESC LIMIT ? OFFSET ?",
            tuple(params),
        )
        return [_row_to_trade(r) for r in rows]

    async def get_by_coin(
        self,
        coin: str,
        limit: int = 50,
        mode: BotMode | str | None = None,
    ) -> list[Trade]:
        conditions = ["coin=?"]
        params: list = [coin]
        if mode:
            conditions.append("mode=?")
            params.append(mode.value if hasattr(mode, "value") else str(mode))

        where = " AND ".join(conditions)
        params.append(limit)
        rows = await self._fetchall(
            f"SELECT * FROM trades WHERE {where} ORDER BY exit_time DESC LIMIT ?",
            tuple(params),
        )
        return [_row_to_trade(r) for r in rows]

    async def get_by_position(self, position_id: str) -> list[Trade]:
        rows = await self._fetchall(
            "SELECT * FROM trades WHERE position_id=? ORDER BY exit_time DESC",
            (position_id,),
        )
        return [_row_to_trade(r) for r in rows]

    async def get_since(
        self,
        since: datetime,
        limit: int | None = None,
        mode: BotMode | str | None = None,
    ) -> list[Trade]:
        conditions = ["exit_time >= ?"]
        params: list = [since.isoformat()]
        if mode:
            conditions.append("mode=?")
            params.append(mode.value if hasattr(mode, "value") else str(mode))

        where = " AND ".join(conditions)
        if limit:
            params.append(limit)
            rows = await self._fetchall(
                f"SELECT * FROM trades WHERE {where} ORDER BY exit_time DESC LIMIT ?",
                tuple(params),
            )
        else:
            rows = await self._fetchall(
                f"SELECT * FROM trades WHERE {where} ORDER BY exit_time DESC",
                tuple(params),
            )
        return [_row_to_trade(r) for r in rows]

    async def get_recent(
        self,
        limit: int = 50,
        mode: BotMode | str | None = None,
    ) -> list[Trade]:
        if mode:
            mode_str = mode.value if hasattr(mode, "value") else str(mode)
            rows = await self._fetchall(
                "SELECT * FROM trades WHERE mode=? ORDER BY exit_time DESC LIMIT ?",
                (mode_str, limit),
            )
        else:
            rows = await self._fetchall(
                "SELECT * FROM trades ORDER BY exit_time DESC LIMIT ?", (limit,)
            )
        return [_row_to_trade(r) for r in rows]

    async def get_all(
        self,
        limit: int = 5000,
        mode: BotMode | str | None = None,
    ) -> list[Trade]:
        """Fetch all closed trades ordered by exit_time descending."""
        conditions = []
        params: list = []
        if mode:
            conditions.append("mode=?")
            params.append(mode.value if hasattr(mode, "value") else str(mode))
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(limit)
        rows = await self._fetchall(
            f"SELECT * FROM trades {where} ORDER BY exit_time DESC LIMIT ?",
            tuple(params),
        )
        return [_row_to_trade(r) for r in rows]

    async def get_count(
        self,
        mode: BotMode | str | None = None,
    ) -> int:
        """Fetch total count of closed trades."""
        conditions = []
        params: list = []
        if mode:
            conditions.append("mode=?")
            params.append(mode.value if hasattr(mode, "value") else str(mode))
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        row = await self._fetchone(
            f"SELECT COUNT(*) as cnt FROM trades {where}",
            tuple(params),
        )
        return int(row["cnt"]) if row and "cnt" in row else 0

    async def get_win_rate(
        self,
        bot: BotName | None = None,
        since: datetime | None = None,
        mode: BotMode | str | None = None,
    ) -> float:
        """Return fraction of profitable trades (pnl > 0). Returns 0.0 if no trades."""
        conditions = []
        params: list = []
        if bot:
            conditions.append("bot=?")
            params.append(bot.value if hasattr(bot, "value") else str(bot))
        if since:
            conditions.append("exit_time >= ?")
            params.append(since.isoformat())
        if mode:
            conditions.append("mode=?")
            params.append(mode.value if hasattr(mode, "value") else str(mode))
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
        bot: BotName | None = None,
        since: datetime | None = None,
        mode: BotMode | str | None = None,
    ) -> list[tuple[datetime, float]]:
        conditions = []
        params: list = []
        if bot:
            conditions.append("bot=?")
            params.append(bot.value if hasattr(bot, "value") else str(bot))
        if since:
            conditions.append("exit_time >= ?")
            params.append(since.isoformat())
        if mode:
            conditions.append("mode=?")
            params.append(mode.value if hasattr(mode, "value") else str(mode))
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

    async def audit_historical_trades(self) -> list[dict]:
        """
        Diagnostic audit function: scans closed trade history and identifies suspicious or
        corrupted trade records (e.g. entry/exit <= 0, impossible 100x price jumps, extreme PnL)
        without deleting data. Marks them as CORRUPTED/INVALID.
        """
        rows = await self._fetchall("SELECT * FROM trades ORDER BY exit_time DESC")
        suspicious = []
        for r in rows:
            t = _row_to_trade(r)
            issues = []
            if t.entry_price <= 0:
                issues.append(f"Non-positive entry_price: {t.entry_price}")
            if t.exit_price <= 0:
                issues.append(f"Non-positive exit_price: {t.exit_price}")
            if t.qty <= 0:
                issues.append(f"Non-positive qty: {t.qty}")
            if t.entry_price > 0 and t.exit_price > 0:
                ratio = t.exit_price / t.entry_price
                if ratio > 5.0 or ratio < 0.2:
                    issues.append(
                        f"Abnormal price-ratio jump: {ratio:.2f}x (entry: {t.entry_price}, exit: {t.exit_price})"
                    )
            if abs(t.pnl_pct) > 500.0:
                issues.append(f"Extreme PnL percentage: {t.pnl_pct:.2f}%")

            if issues:
                suspicious.append(
                    {
                        "trade_id": t.id,
                        "position_id": t.position_id,
                        "bot": t.bot.value if hasattr(t.bot, "value") else str(t.bot),
                        "coin": t.coin,
                        "pair": t.pair,
                        "entry_price": t.entry_price,
                        "exit_price": t.exit_price,
                        "qty": t.qty,
                        "pnl": t.pnl,
                        "pnl_pct": t.pnl_pct,
                        "status": "CORRUPTED_SUSPICIOUS",
                        "issues": issues,
                    }
                )
        return suspicious
