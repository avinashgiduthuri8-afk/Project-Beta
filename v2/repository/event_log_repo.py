"""
V2 EventLogRepository — append-only audit trail for all bus events.

This table is never updated or selectively deleted; pruning is done
only via prune_before() on a scheduled job.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import aiosqlite

from v2.core.logging import get_logger
from .base import BaseRepository

logger = get_logger("v2.repository.event_log_repo")


@dataclass
class EventLogEntry:
    id:             str
    event_type:     str
    source_service: Optional[str]
    entity_id:      Optional[str]
    payload:        dict
    logged_at:      datetime


def _dt(s: str | None) -> Optional[datetime]:
    if s is None:
        return None
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _row_to_entry(row: aiosqlite.Row, loads) -> EventLogEntry:
    d = dict(row)
    return EventLogEntry(
        id             = d["id"],
        event_type     = d["event_type"],
        source_service = d.get("source_service"),
        entity_id      = d.get("entity_id"),
        payload        = loads(d.get("payload_json")) or {},
        logged_at      = _dt(d["logged_at"]),
    )


class EventLogRepository(BaseRepository):

    async def append(
        self,
        event_type: str,
        payload: dict,
        source_service: Optional[str] = None,
        entity_id: Optional[str] = None,
    ) -> str:
        entry_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        await self._execute(
            """
            INSERT INTO event_log
            (id, event_type, source_service, entity_id, payload_json, logged_at)
            VALUES (?,?,?,?,?,?)
            """,
            (entry_id, event_type, source_service, entity_id,
             self._dumps(payload), now),
        )
        return entry_id

    async def get_since(
        self, since: datetime, limit: int = 500
    ) -> list[EventLogEntry]:
        rows = await self._fetchall(
            "SELECT * FROM event_log WHERE logged_at >= ? "
            "ORDER BY logged_at DESC LIMIT ?",
            (since.isoformat(), limit),
        )
        return [_row_to_entry(r, self._loads) for r in rows]

    async def get_by_type(
        self, event_type: str, limit: int = 100
    ) -> list[EventLogEntry]:
        rows = await self._fetchall(
            "SELECT * FROM event_log WHERE event_type=? "
            "ORDER BY logged_at DESC LIMIT ?",
            (event_type, limit),
        )
        return [_row_to_entry(r, self._loads) for r in rows]

    async def get_by_entity(self, entity_id: str) -> list[EventLogEntry]:
        rows = await self._fetchall(
            "SELECT * FROM event_log WHERE entity_id=? ORDER BY logged_at ASC",
            (entity_id,),
        )
        return [_row_to_entry(r, self._loads) for r in rows]

    async def prune_before(self, cutoff: datetime) -> int:
        cur = await self._execute(
            "DELETE FROM event_log WHERE logged_at < ?",
            (cutoff.isoformat(),),
        )
        return cur.rowcount
