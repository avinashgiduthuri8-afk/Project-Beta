"""
V2 Feedback Repository.

Asynchronous SQLite interface for persisting feedback audit trail events,
pre-deployment backtest validation outcomes, and active strategy calibrations cache.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from core.logging import get_logger

logger = get_logger("core.repository.feedback_repo")


class FeedbackRepository:
    """Repository for managing autonomous feedback audit trails and calibrations cache in SQLite."""

    def __init__(self, conn: sqlite3.Connection | aiosqlite.Connection) -> None:
        self._conn = conn

    def _is_async(self) -> bool:
        return isinstance(self._conn, aiosqlite.Connection)

    async def _execute(self, query: str, params: tuple = ()) -> Any:
        if self._is_async():
            return await self._conn.execute(query, params)
        else:
            return self._conn.execute(query, params)

    async def _commit(self) -> None:
        if self._is_async():
            await self._conn.commit()
        else:
            self._conn.commit()

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

    # ── Feedback Audit Trail ──────────────────────────────────────────────────

    async def record_audit_event(self, event: dict[str, Any]) -> str:
        """Insert a feedback audit event into SQLite."""
        event_id = str(event.get("id") or uuid.uuid4())
        cycle_id = str(event.get("cycle_id") or uuid.uuid4())
        now_str = datetime.now(timezone.utc).isoformat()

        query = """
        INSERT INTO feedback_audit_trail (
            id, cycle_id, bot_name, pair, action_taken, previous_multiplier, new_multiplier,
            previous_threshold, new_threshold, validation_backtest_id, status,
            validation_candle_count, validation_oos_candle_count,
            validation_trade_count, validation_profit_factor, validation_reason,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            event_id,
            cycle_id,
            str(event["bot_name"]).upper(),
            event.get("pair"),
            str(event["action_taken"]).upper(),
            float(event.get("previous_multiplier", 1.0)),
            float(event.get("new_multiplier", 1.0)),
            float(event.get("previous_threshold", 85.0)),
            float(event.get("new_threshold", 85.0)),
            event.get("validation_backtest_id"),
            str(event.get("status", "PROMOTED")).upper(),
            event.get("validation_candle_count"),
            event.get("validation_oos_candle_count"),
            event.get("validation_trade_count"),
            event.get("validation_profit_factor"),
            event.get("validation_reason"),
            str(event.get("created_at") or now_str),
        )

        await self._execute(query, params)
        await self._commit()

        logger.info(
            "Recorded feedback audit event %s [%s] for bot %s: status=%s",
            event_id,
            event["action_taken"],
            event["bot_name"],
            event.get("status", "PROMOTED"),
        )
        return event_id

    async def get_audit_history(
        self, bot_name: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Fetch chronological audit trail records."""
        if bot_name:
            query = "SELECT * FROM feedback_audit_trail WHERE bot_name = ? ORDER BY created_at DESC LIMIT ?"
            return await self._fetchall(query, (bot_name.upper(), limit))
        else:
            query = (
                "SELECT * FROM feedback_audit_trail ORDER BY created_at DESC LIMIT ?"
            )
            return await self._fetchall(query, (limit,))

    # ── Active Calibrations Cache ─────────────────────────────────────────────

    async def upsert_active_calibration(
        self, bot_name: str, weight_multiplier: float, strict_threshold: float
    ) -> None:
        """Upsert active promoted calibration configuration into cache."""
        bot_str = bot_name.upper()
        now_str = datetime.now(timezone.utc).isoformat()

        query = """
        INSERT INTO active_calibrations_cache (
            bot_name, weight_multiplier, strict_threshold, updated_at
        ) VALUES (?, ?, ?, ?)
        ON CONFLICT(bot_name) DO UPDATE SET
            weight_multiplier = excluded.weight_multiplier,
            strict_threshold = excluded.strict_threshold,
            updated_at = excluded.updated_at
        """
        params = (bot_str, float(weight_multiplier), float(strict_threshold), now_str)

        await self._execute(query, params)
        await self._commit()

        logger.info(
            "Updated active calibration cache for bot %s -> Mult: %.2fx, Thresh: %.1f",
            bot_str,
            weight_multiplier,
            strict_threshold,
        )

    async def get_active_calibration(self, bot_name: str) -> dict[str, Any] | None:
        """Fetch cached active calibration for a bot strategy."""
        query = "SELECT * FROM active_calibrations_cache WHERE bot_name = ?"
        return await self._fetchone(query, (bot_name.upper(),))

    async def get_all_active_calibrations(self) -> list[dict[str, Any]]:
        """Fetch all cached active calibrations."""
        query = "SELECT * FROM active_calibrations_cache ORDER BY bot_name ASC"
        return await self._fetchall(query)

    async def activate_canary(
        self, bot_name: str, promotion_id: str, promoted_at: str
    ) -> None:
        """Persist a promotion monitor so restarts cannot reset rollback state."""
        now_str = datetime.now(timezone.utc).isoformat()
        await self._execute(
            """
            INSERT INTO calibration_canary_state (
                bot_name, promotion_id, promoted_at, consecutive_losses, active, updated_at
            ) VALUES (?, ?, ?, 0, 1, ?)
            ON CONFLICT(bot_name) DO UPDATE SET
                promotion_id = excluded.promotion_id,
                promoted_at = excluded.promoted_at,
                consecutive_losses = 0,
                active = 1,
                updated_at = excluded.updated_at
            """,
            (bot_name.upper(), promotion_id, promoted_at, now_str),
        )
        await self._commit()

    async def get_canary_state(self, bot_name: str) -> dict[str, Any] | None:
        return await self._fetchone(
            "SELECT * FROM calibration_canary_state WHERE bot_name = ? AND active = 1",
            (bot_name.upper(),),
        )

    async def claim_calibration_outcome(
        self, trade_id: str, bot_name: str, promotion_id: str
    ) -> bool:
        """Return True only once for a trade/promotion outcome."""
        cursor = await self._execute(
            """
            INSERT OR IGNORE INTO processed_calibration_outcomes (
                trade_id, bot_name, promotion_id, processed_at
            ) VALUES (?, ?, ?, ?)
            """,
            (
                trade_id,
                bot_name.upper(),
                promotion_id,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await self._commit()
        return int(cursor.rowcount or 0) == 1

    async def update_canary_losses(
        self, bot_name: str, consecutive_losses: int, *, active: bool = True
    ) -> None:
        await self._execute(
            """
            UPDATE calibration_canary_state
            SET consecutive_losses = ?, active = ?, updated_at = ?
            WHERE bot_name = ?
            """,
            (
                int(consecutive_losses),
                1 if active else 0,
                datetime.now(timezone.utc).isoformat(),
                bot_name.upper(),
            ),
        )
        await self._commit()
