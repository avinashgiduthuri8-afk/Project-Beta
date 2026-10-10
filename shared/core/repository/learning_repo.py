"""
V2 Learning Repository.

Asynchronous SQLite interface for persisting learned insights, mistake diagnoses,
and dynamic strategy calibrations (weight multipliers & confluence score thresholds).
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from core.logging import get_logger

logger = get_logger("core.repository.learning_repo")


class LearningRepository:
    """Repository for managing learning insights and strategy calibrations in SQLite."""

    def __init__(self, conn: sqlite3.Connection | aiosqlite.Connection) -> None:
        self._conn = conn

    def _is_async(self) -> bool:
        return isinstance(self._conn, aiosqlite.Connection)

    async def _execute(self, query: str, params: tuple = ()) -> Any:
        if self._is_async():
            return await self._conn.execute(query, params)
        else:
            return self._conn.execute(query, params)

    async def _fetchall(self, query: str, params: tuple = ()) -> list[dict[str, Any]]:
        if self._is_async():
            async with self._conn.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                cols = [description[0] for description in cursor.description]
                return [dict(zip(cols, r)) for r in rows]
        else:
            cur = self._conn.execute(query, params)
            rows = cur.fetchall()
            cols = [description[0] for description in cur.description]
            return [dict(zip(cols, r)) for r in rows]

    async def _fetchone(self, query: str, params: tuple = ()) -> dict[str, Any] | None:
        rows = await self._fetchall(query, params)
        return rows[0] if rows else None

    # ── Learning Insights ─────────────────────────────────────────────────────

    async def record_insight(self, insight: dict[str, Any]) -> str:
        """Insert a learned insight record into SQLite."""
        insight_id = str(insight.get("id") or uuid.uuid4())
        now_str = datetime.now(timezone.utc).isoformat()

        query = """
        INSERT INTO learning_insights (
            id, bot_name, pair, pattern_type, severity, lesson_summary, recommended_adjustment, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            insight_id,
            insight.get("bot_name"),
            insight.get("pair"),
            str(insight["pattern_type"]),
            str(insight["severity"]),
            str(insight["lesson_summary"]),
            str(insight["recommended_adjustment"]),
            str(insight.get("created_at") or now_str),
        )

        await self._execute(query, params)
        if not self._is_async():
            self._conn.commit()

        logger.info(
            "Recorded learning insight %s [%s] for bot %s: %s",
            insight_id,
            insight["pattern_type"],
            insight.get("bot_name"),
            insight["lesson_summary"],
        )
        return insight_id

    async def get_active_insights(
        self,
        bot_name: str | None = None,
        pair: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Fetch recent learning insights filtered by optional bot_name or pair."""
        conditions: list[str] = []
        params: list[Any] = []

        if bot_name:
            conditions.append("bot_name = ?")
            params.append(bot_name.upper())

        if pair:
            conditions.append("pair = ?")
            params.append(pair.upper())

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"SELECT * FROM learning_insights {where_clause} ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        return await self._fetchall(query, tuple(params))

    # ── Strategy Calibrations ─────────────────────────────────────────────────

    async def upsert_calibration(
        self,
        bot_name: str,
        pair: str | None = None,
        weight_multiplier: float = 1.0,
        min_confluence_threshold: float = 85.0,
        status: str = "ACTIVE",
    ) -> None:
        """Upsert a strategy calibration record for a trading bot."""
        now_str = datetime.now(timezone.utc).isoformat()
        cal_id = str(uuid.uuid4())
        bot_str = bot_name.upper()

        query = """
        INSERT INTO strategy_calibrations (
            id, bot_name, pair, weight_multiplier, min_confluence_threshold, status, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(bot_name) DO UPDATE SET
            pair = excluded.pair,
            weight_multiplier = excluded.weight_multiplier,
            min_confluence_threshold = excluded.min_confluence_threshold,
            status = excluded.status,
            updated_at = excluded.updated_at
        """
        params = (
            cal_id,
            bot_str,
            pair,
            float(weight_multiplier),
            float(min_confluence_threshold),
            str(status).upper(),
            now_str,
        )

        await self._execute(query, params)
        if not self._is_async():
            self._conn.commit()

        logger.info(
            "Upserted calibration for bot %s -> status=%s, multiplier=%.2f, threshold=%.1f",
            bot_str,
            status,
            weight_multiplier,
            min_confluence_threshold,
        )

    async def get_calibrations(self) -> list[dict[str, Any]]:
        """Fetch all active strategy calibrations."""
        query = "SELECT * FROM strategy_calibrations ORDER BY bot_name ASC"
        return await self._fetchall(query)

    async def get_calibration_for_bot(self, bot_name: str) -> dict[str, Any] | None:
        """Fetch strategy calibration for a specific bot."""
        query = "SELECT * FROM strategy_calibrations WHERE bot_name = ?"
        return await self._fetchone(query, (bot_name.upper(),))
