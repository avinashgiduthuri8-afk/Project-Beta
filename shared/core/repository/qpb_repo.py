"""Persistence for Phase 4 QPB signal decisions and allocations."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .base import BaseRepository


class QPBRepository(BaseRepository):
    """Durable idempotency, ranking, allocation, and rejection ledger."""

    async def claim(self, signal_id: str, payload: dict[str, Any]) -> bool:
        """Atomically claim a signal. False means it was processed before."""
        now = datetime.now(timezone.utc).isoformat()
        cursor = await self._execute(
            """
            INSERT OR IGNORE INTO qpb_signal_decisions
            (signal_id, symbol, strategy, strategy_name, status, payload_json,
             created_at, updated_at)
            VALUES (?, ?, ?, ?, 'RECEIVED', ?, ?, ?)
            """,
            (
                signal_id,
                str(payload.get("symbol") or payload.get("coin") or "UNKNOWN"),
                str(payload.get("strategy") or "UNKNOWN"),
                str(payload.get("strategy_name") or payload.get("strategy") or "UNKNOWN"),
                self._dumps(payload),
                now,
                now,
            ),
        )
        return cursor.rowcount == 1

    async def update(
        self,
        signal_id: str,
        *,
        status: str,
        ranking_score: float | None = None,
        allocated_amount: float | None = None,
        reason_code: str | None = None,
        reason: str | None = None,
        position_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self._execute(
            """
            UPDATE qpb_signal_decisions
            SET status=?,
                ranking_score=COALESCE(?, ranking_score),
                allocated_amount=COALESCE(?, allocated_amount),
                reason_code=?, reason=?,
                position_id=COALESCE(?, position_id),
                payload_json=COALESCE(?, payload_json),
                updated_at=?
            WHERE signal_id=?
            """,
            (
                status,
                ranking_score,
                allocated_amount,
                reason_code,
                reason,
                position_id,
                self._dumps(payload) if payload is not None else None,
                now,
                signal_id,
            ),
        )

    async def get(self, signal_id: str) -> dict[str, Any] | None:
        row = await self._fetchone(
            "SELECT * FROM qpb_signal_decisions WHERE signal_id=?", (signal_id,)
        )
        return self._row(row) if row else None

    async def list_recent(
        self, *, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        if status:
            rows = await self._fetchall(
                "SELECT * FROM qpb_signal_decisions WHERE status=? "
                "ORDER BY updated_at DESC LIMIT ?",
                (status, limit),
            )
        else:
            rows = await self._fetchall(
                "SELECT * FROM qpb_signal_decisions "
                "ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            )
        return [self._row(row) for row in rows]

    async def list_rankings(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self._fetchall(
            """
            SELECT * FROM qpb_signal_decisions
            WHERE ranking_score IS NOT NULL
            ORDER BY ranking_score DESC, signal_id ASC
            LIMIT ?
            """,
            (limit,),
        )
        return [self._row(row) for row in rows]

    async def counts_by_status(self) -> dict[str, int]:
        rows = await self._fetchall(
            "SELECT status, COUNT(*) AS count FROM qpb_signal_decisions GROUP BY status"
        )
        return {str(row["status"]): int(row["count"]) for row in rows}

    @classmethod
    def _row(cls, row: Any) -> dict[str, Any]:
        result = dict(row)
        result["payload"] = cls._loads(result.pop("payload_json", None)) or {}
        return result
