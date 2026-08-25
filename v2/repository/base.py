"""
V2 BaseRepository — abstract base class for all V2 repositories.

Provides thin wrappers around aiosqlite that handle connection
acquisition and basic error translation.  Subclasses never import
aiosqlite directly.
"""

from __future__ import annotations

import json
from abc import ABC
from typing import Any, Optional

import aiosqlite

from v2.core.exceptions import StorageError
from v2.core.logging import get_logger

logger = get_logger("v2.repository.base")


class BaseRepository(ABC):
    """
    Abstract base for all V2 repositories.

    Requires a live aiosqlite.Connection injected at construction.
    The connection's lifetime is managed by db.py — repositories
    do not open or close connections.
    """

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    # ── Low-level helpers ─────────────────────────────────────────────────────

    async def _execute(
        self,
        sql: str,
        params: tuple | None = None,
    ) -> aiosqlite.Cursor:
        try:
            cursor = await self._conn.execute(sql, params or ())
            await self._conn.commit()
            return cursor
        except Exception as exc:
            raise StorageError(f"SQL execute failed: {exc}\nSQL: {sql}") from exc

    async def _fetchone(
        self,
        sql: str,
        params: tuple | None = None,
    ) -> Optional[aiosqlite.Row]:
        try:
            async with self._conn.execute(sql, params or ()) as cur:
                return await cur.fetchone()
        except Exception as exc:
            raise StorageError(f"SQL fetchone failed: {exc}\nSQL: {sql}") from exc

    async def _fetchall(
        self,
        sql: str,
        params: tuple | None = None,
    ) -> list[aiosqlite.Row]:
        try:
            async with self._conn.execute(sql, params or ()) as cur:
                return await cur.fetchall()
        except Exception as exc:
            raise StorageError(f"SQL fetchall failed: {exc}\nSQL: {sql}") from exc

    async def _fetchmany(
        self,
        sql: str,
        params: tuple | None = None,
        limit: int = 100,
    ) -> list[aiosqlite.Row]:
        try:
            async with self._conn.execute(sql, params or ()) as cur:
                return await cur.fetchmany(limit)
        except Exception as exc:
            raise StorageError(f"SQL fetchmany failed: {exc}\nSQL: {sql}") from exc

    # ── JSON helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _dumps(obj: Any) -> str:
        return json.dumps(obj, default=str)

    @staticmethod
    def _loads(s: str | None) -> Any:
        if s is None:
            return None
        return json.loads(s)
