"""
V2 Trade Journal Repository.

Asynchronous SQLite interface for persisting and querying detailed post-trade
journal entries, statutory fee breakdowns, MFE/MAE excursions, and strategy tags.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import aiosqlite

from core.logging import get_logger

logger = get_logger("core.repository.journal_repo")


class JournalRepository:
    """Repository for managing post-trade journal entries in SQLite."""

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

    async def insert_entry(self, entry: dict[str, Any]) -> str:
        """Insert a complete post-trade journal entry into SQLite."""
        tags_raw = entry.get("tags")
        if isinstance(tags_raw, (list, dict)):
            tags_json = json.dumps(tags_raw)
        else:
            tags_json = str(tags_raw or "[]")

        query = """
        INSERT INTO trade_journal (
            id, position_id, bot_name, pair, side, entry_price, exit_price,
            quantity, entry_timestamp, exit_timestamp, duration_seconds, exit_reason,
            gross_pnl, exchange_fee, gst_tax, tds_194s, slippage_cost,
            total_statutory_drag, net_pnl, net_pnl_pct, mfe, mae, tags
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?
        )
        """
        params = (
            entry["id"],
            entry["position_id"],
            entry["bot_name"],
            entry["pair"],
            entry.get("side", "BUY"),
            float(entry["entry_price"]),
            float(entry["exit_price"]),
            float(entry["quantity"]),
            str(entry["entry_timestamp"]),
            str(entry["exit_timestamp"]),
            int(entry["duration_seconds"]),
            str(entry["exit_reason"]),
            float(entry["gross_pnl"]),
            float(entry["exchange_fee"]),
            float(entry["gst_tax"]),
            float(entry["tds_194s"]),
            float(entry["slippage_cost"]),
            float(entry["total_statutory_drag"]),
            float(entry["net_pnl"]),
            float(entry["net_pnl_pct"]),
            float(entry["mfe"]) if entry.get("mfe") is not None else None,
            float(entry["mae"]) if entry.get("mae") is not None else None,
            tags_json,
        )

        await self._execute(query, params)
        await self._commit()

        logger.info(
            "Persisted trade journal entry %s for position %s [%s] (Net PnL: INR %.2f)",
            entry["id"],
            entry["position_id"],
            entry["bot_name"],
            float(entry["net_pnl"]),
        )
        return entry["id"]

    async def get_entries(
        self,
        limit: int = 50,
        offset: int = 0,
        bot_name: str | None = None,
        pair: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch paginated trade journal entries filtered by optional bot_name or pair."""
        conditions: list[str] = []
        params: list[Any] = []

        if bot_name:
            conditions.append("bot_name = ?")
            params.append(bot_name.upper())

        if pair:
            conditions.append("pair = ?")
            params.append(pair.upper())

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"SELECT * FROM trade_journal {where_clause} ORDER BY exit_timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = await self._fetchall(query, tuple(params))
        for r in rows:
            if r.get("tags") and isinstance(r["tags"], str):
                try:
                    r["tags"] = json.loads(r["tags"])
                except Exception:
                    pass
        return rows

    async def get_entries_by_timerange(
        self, start_iso: str, end_iso: str
    ) -> list[dict[str, Any]]:
        """Fetch all trade journal entries between start_iso and end_iso."""
        query = "SELECT * FROM trade_journal WHERE exit_timestamp >= ? AND exit_timestamp <= ? ORDER BY exit_timestamp ASC"
        rows = await self._fetchall(query, (start_iso, end_iso))
        for r in rows:
            if r.get("tags") and isinstance(r["tags"], str):
                try:
                    r["tags"] = json.loads(r["tags"])
                except Exception:
                    pass
        return rows

    async def get_all_journal_entries(self) -> list[dict[str, Any]]:
        """Fetch all trade journal entries ordered chronologically."""
        query = "SELECT * FROM trade_journal ORDER BY exit_timestamp ASC"
        rows = await self._fetchall(query)
        for r in rows:
            if r.get("tags") and isinstance(r["tags"], str):
                try:
                    r["tags"] = json.loads(r["tags"])
                except Exception:
                    pass
        return rows
