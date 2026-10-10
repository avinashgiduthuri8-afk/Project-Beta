"""
V2 ProductionStateRepository — Persistent operational state and circuit breaker tracking.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.logging import get_logger

from .base import BaseRepository

logger = get_logger("core.repository.production_state_repo")


class ProductionStateRepository(BaseRepository):
    """
    Manages persistent runtime operational state for PROJECT-ALPHA V2 in SQLite.
    Survives server restarts and crashes.
    """

    def _canonical_key(self, key: str) -> str:
        if key.startswith("v2_"):
            return key[3:]
        return key

    async def get(self, key: str) -> str | None:
        """Fetch value for a specific runtime key (supports canonical & legacy aliases)."""
        canon = self._canonical_key(key)
        async with self._conn.execute(
            "SELECT value FROM production_runtime_state WHERE key IN (?, ?, ?)",
            (key, canon, f"v2_{canon}"),
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None

    async def set(self, key: str, value: Any, updated_by: str = "SYSTEM") -> None:
        """Insert or update a runtime key-value pair (persists both canonical and legacy alias)."""
        now_str = datetime.now(timezone.utc).isoformat()
        val_str = str(value)
        canon = self._canonical_key(key)
        keys_to_write = {key, canon, f"v2_{canon}"}
        params = [(k, val_str, now_str, updated_by) for k in keys_to_write]
        await self._conn.executemany(
            """
            INSERT INTO production_runtime_state (key, value, updated_at, updated_by)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at,
                updated_by = excluded.updated_by
            """,
            params,
        )
        await self._conn.commit()

    async def set_many(self, items: dict[str, Any], updated_by: str = "SYSTEM") -> None:
        """Atomically insert or update multiple runtime keys (persists both canonical and legacy alias)."""
        now_str = datetime.now(timezone.utc).isoformat()
        params = []
        for k, v in items.items():
            canon = self._canonical_key(k)
            val_str = str(v)
            for key_variant in {k, canon, f"v2_{canon}"}:
                params.append((key_variant, val_str, now_str, updated_by))
        await self._conn.executemany(
            """
            INSERT INTO production_runtime_state (key, value, updated_at, updated_by)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at,
                updated_by = excluded.updated_by
            """,
            params,
        )
        await self._conn.commit()

    async def get_all(self) -> dict[str, str]:
        """Retrieve all persisted runtime state keys and values."""
        async with self._conn.execute(
            "SELECT key, value FROM production_runtime_state"
        ) as cur:
            rows = await cur.fetchall()
            return {row[0]: row[1] for row in rows}

    async def verify_integrity(self) -> bool:
        """Run SQLite integrity check to verify database health."""
        try:
            async with self._conn.execute("PRAGMA integrity_check") as cur:
                rows = await cur.fetchall()
                if rows and rows[0][0].lower() == "ok":
                    return True
                logger.error("Database integrity check failed: %s", rows)
                return False
        except Exception as exc:
            logger.error("Failed to run PRAGMA integrity_check: %s", exc)
            return False
