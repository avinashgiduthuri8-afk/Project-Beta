"""
V2 ShadowRepository — persistence for simulated shadow trades and divergence tracking.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import aiosqlite

from core.logging import get_logger
from core.types import BotName, DecisionDivergence, ShadowTrade

from .base import BaseRepository

logger = get_logger("core.repository.shadow_repo")

_ISO = "%Y-%m-%dT%H:%M:%S.%f+00:00"


def _dt(s: str | None) -> datetime | None:
    if s is None:
        return None
    for fmt in (
        _ISO,
        "%Y-%m-%dT%H:%M:%S+00:00",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _row_to_shadow_trade(row: aiosqlite.Row) -> ShadowTrade:
    d = dict(row)
    adjustments = BaseRepository._loads(d.get("raw_adjustments")) or {}

    return ShadowTrade(
        id=d["id"],
        signal_id=d["signal_id"],
        bot=BotName(d["bot"]),
        coin=d["coin"],
        pair=d["pair"],
        entry_price=float(d["entry_price"]),
        qty=float(d["qty"]),
        amount=float(d["amount"]),
        stop_loss=float(d["stop_loss"]) if d.get("stop_loss") is not None else None,
        take_profit=(
            float(d["take_profit"]) if d.get("take_profit") is not None else None
        ),
        ai_recommendation=d.get("ai_recommendation"),
        status=d.get("status", "OPEN"),
        simulated_exit_price=(
            float(d["simulated_exit_price"])
            if d.get("simulated_exit_price") is not None
            else None
        ),
        simulated_pnl=(
            float(d["simulated_pnl"]) if d.get("simulated_pnl") is not None else None
        ),
        simulated_pnl_pct=(
            float(d["simulated_pnl_pct"])
            if d.get("simulated_pnl_pct") is not None
            else None
        ),
        exit_reason=d.get("exit_reason"),
        created_at=_dt(d["created_at"]) or datetime.now(timezone.utc),
        closed_at=_dt(d.get("closed_at")),
        raw_adjustments=adjustments if isinstance(adjustments, dict) else {},
    )


def _row_to_divergence(row: aiosqlite.Row) -> DecisionDivergence:
    d = dict(row)
    return DecisionDivergence(
        id=d["id"],
        signal_id=d["signal_id"],
        bot=BotName(d["bot"]),
        coin=d["coin"],
        v1_action=d["v1_action"],
        v2_action=d["v2_action"],
        divergence_type=d["divergence_type"],
        reason=d["reason"],
        detected_at=_dt(d["detected_at"]) or datetime.now(timezone.utc),
        v1_pnl=float(d["v1_pnl"]) if d.get("v1_pnl") is not None else None,
        v2_simulated_pnl=(
            float(d["v2_simulated_pnl"])
            if d.get("v2_simulated_pnl") is not None
            else None
        ),
    )


class ShadowRepository(BaseRepository):
    """Persistence operations for shadow simulation and divergence tracking."""

    # ── Shadow Trades ─────────────────────────────────────────────────────────

    async def insert_shadow_trade(self, trade: ShadowTrade) -> str:
        if not trade.id:
            trade.id = str(uuid.uuid4())

        await self._execute(
            """
            INSERT OR REPLACE INTO shadow_trades
            (id, signal_id, bot, coin, pair, entry_price, qty, amount,
             stop_loss, take_profit, ai_recommendation, status,
             simulated_exit_price, simulated_pnl, simulated_pnl_pct,
             exit_reason, created_at, closed_at, raw_adjustments)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                trade.id,
                trade.signal_id,
                trade.bot.value,
                trade.coin,
                trade.pair,
                trade.entry_price,
                trade.qty,
                trade.amount,
                trade.stop_loss,
                trade.take_profit,
                trade.ai_recommendation,
                trade.status,
                trade.simulated_exit_price,
                trade.simulated_pnl,
                trade.simulated_pnl_pct,
                trade.exit_reason,
                trade.created_at.isoformat(),
                trade.closed_at.isoformat() if trade.closed_at else None,
                self._dumps(trade.raw_adjustments),
            ),
        )
        return trade.id

    async def update_shadow_trade(self, trade: ShadowTrade) -> None:
        await self._execute(
            """
            UPDATE shadow_trades
            SET status=?, simulated_exit_price=?, simulated_pnl=?,
                simulated_pnl_pct=?, exit_reason=?, closed_at=?
            WHERE id=?
            """,
            (
                trade.status,
                trade.simulated_exit_price,
                trade.simulated_pnl,
                trade.simulated_pnl_pct,
                trade.exit_reason,
                trade.closed_at.isoformat() if trade.closed_at else None,
                trade.id,
            ),
        )

    async def get_shadow_trade_by_id(self, trade_id: str) -> ShadowTrade | None:
        row = await self._fetchone(
            "SELECT * FROM shadow_trades WHERE id=?", (trade_id,)
        )
        return _row_to_shadow_trade(row) if row else None

    async def get_open_shadow_trades(
        self, bot: BotName | None = None
    ) -> list[ShadowTrade]:
        if bot is not None:
            rows = await self._fetchall(
                "SELECT * FROM shadow_trades WHERE status='OPEN' AND bot=? ORDER BY created_at ASC",
                (bot.value,),
            )
        else:
            rows = await self._fetchall(
                "SELECT * FROM shadow_trades WHERE status='OPEN' ORDER BY created_at ASC"
            )
        return [_row_to_shadow_trade(r) for r in rows]

    async def get_shadow_trades_by_coin(
        self, coin: str, limit: int = 20
    ) -> list[ShadowTrade]:
        rows = await self._fetchall(
            "SELECT * FROM shadow_trades WHERE coin=? ORDER BY created_at DESC LIMIT ?",
            (coin.upper(), limit),
        )
        return [_row_to_shadow_trade(r) for r in rows]

    async def get_recent_shadow_trades(
        self, limit: int = 50, status: str | None = None
    ) -> list[ShadowTrade]:
        if status:
            rows = await self._fetchall(
                "SELECT * FROM shadow_trades WHERE status=? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            )
        else:
            rows = await self._fetchall(
                "SELECT * FROM shadow_trades ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        return [_row_to_shadow_trade(r) for r in rows]

    # ── Decision Divergences ──────────────────────────────────────────────────

    async def insert_divergence(self, divergence: DecisionDivergence) -> str:
        if not divergence.id:
            divergence.id = str(uuid.uuid4())

        await self._execute(
            """
            INSERT OR REPLACE INTO decision_divergences
            (id, signal_id, bot, coin, v1_action, v2_action,
             divergence_type, reason, detected_at, v1_pnl, v2_simulated_pnl)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                divergence.id,
                divergence.signal_id,
                divergence.bot.value,
                divergence.coin,
                divergence.v1_action,
                divergence.v2_action,
                divergence.divergence_type,
                divergence.reason,
                divergence.detected_at.isoformat(),
                divergence.v1_pnl,
                divergence.v2_simulated_pnl,
            ),
        )
        return divergence.id

    async def get_divergences(
        self, limit: int = 50, divergence_type: str | None = None
    ) -> list[DecisionDivergence]:
        if divergence_type:
            rows = await self._fetchall(
                "SELECT * FROM decision_divergences WHERE divergence_type=? ORDER BY detected_at DESC LIMIT ?",
                (divergence_type, limit),
            )
        else:
            rows = await self._fetchall(
                "SELECT * FROM decision_divergences ORDER BY detected_at DESC LIMIT ?",
                (limit,),
            )
        return [_row_to_divergence(r) for r in rows]

    async def get_divergence_summary(self) -> dict:
        rows = await self._fetchall(
            "SELECT divergence_type, COUNT(*) as count FROM decision_divergences GROUP BY divergence_type"
        )
        counts_by_type = {r["divergence_type"]: r["count"] for r in rows}

        trade_stats = await self._fetchone("""
            SELECT COUNT(*) as total_shadow,
                   SUM(CASE WHEN status LIKE 'CLOSED_%' THEN 1 ELSE 0 END) as closed_shadow,
                   SUM(CASE WHEN simulated_pnl > 0 THEN 1 ELSE 0 END) as winning_shadow,
                   SUM(COALESCE(simulated_pnl, 0.0)) as total_simulated_pnl
            FROM shadow_trades
            """)

        d = dict(trade_stats) if trade_stats else {}
        total_closed = d.get("closed_shadow") or 0
        winning = d.get("winning_shadow") or 0
        win_rate = (
            round((winning / total_closed) * 100.0, 1) if total_closed > 0 else 0.0
        )

        return {
            "total_divergences": sum(counts_by_type.values()),
            "divergences_by_type": counts_by_type,
            "total_shadow_trades": d.get("total_shadow") or 0,
            "closed_shadow_trades": total_closed,
            "winning_shadow_trades": winning,
            "simulated_win_rate_pct": win_rate,
            "total_simulated_pnl": round(float(d.get("total_simulated_pnl") or 0.0), 2),
        }
