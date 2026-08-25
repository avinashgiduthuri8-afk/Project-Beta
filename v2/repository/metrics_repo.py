"""
V2 MetricsRepository — time-series portfolio snapshots.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any, Optional

import aiosqlite

from v2.core.logging import get_logger
from .base import BaseRepository

logger = get_logger("v2.repository.metrics_repo")


@dataclass
class MetricsSnapshot:
    id:              str
    captured_at:     datetime
    total_aum:       float = 0.0
    total_deployed:  float = 0.0
    total_cash:      float = 0.0
    total_unrealised: float = 0.0
    total_realised:  float = 0.0
    daily_pnl:       float = 0.0
    capital_util_pct: float = 0.0
    per_bot:         dict  = field(default_factory=dict)


def _dt(s: str | None) -> Optional[datetime]:
    if s is None:
        return None
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _row_to_snapshot(row: aiosqlite.Row, loads) -> MetricsSnapshot:
    d = dict(row)
    return MetricsSnapshot(
        id               = d["id"],
        captured_at      = _dt(d["captured_at"]),
        total_aum        = d.get("total_aum") or 0.0,
        total_deployed   = d.get("total_deployed") or 0.0,
        total_cash       = d.get("total_cash") or 0.0,
        total_unrealised = d.get("total_unrealised") or 0.0,
        total_realised   = d.get("total_realised") or 0.0,
        daily_pnl        = d.get("daily_pnl") or 0.0,
        capital_util_pct = d.get("capital_util_pct") or 0.0,
        per_bot          = loads(d.get("per_bot_json")) or {},
    )


class MetricsRepository(BaseRepository):

    async def insert_snapshot(self, snapshot: MetricsSnapshot) -> str:
        sid = snapshot.id or str(uuid.uuid4())
        await self._execute(
            """
            INSERT INTO metrics_snapshots
            (id, captured_at, total_aum, total_deployed, total_cash,
             total_unrealised, total_realised, daily_pnl, capital_util_pct, per_bot_json)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                sid,
                snapshot.captured_at.isoformat(),
                snapshot.total_aum,
                snapshot.total_deployed,
                snapshot.total_cash,
                snapshot.total_unrealised,
                snapshot.total_realised,
                snapshot.daily_pnl,
                snapshot.capital_util_pct,
                self._dumps(snapshot.per_bot),
            ),
        )
        return sid

    async def get_latest(self) -> Optional[MetricsSnapshot]:
        row = await self._fetchone(
            "SELECT * FROM metrics_snapshots ORDER BY captured_at DESC LIMIT 1"
        )
        return _row_to_snapshot(row, self._loads) if row else None

    async def get_series(
        self, metric: str, since: datetime
    ) -> list[tuple[datetime, float]]:
        valid = {
            "total_aum", "total_deployed", "total_cash",
            "daily_pnl", "capital_util_pct",
        }
        if metric not in valid:
            return []
        rows = await self._fetchall(
            f"SELECT captured_at, {metric} FROM metrics_snapshots "
            f"WHERE captured_at >= ? ORDER BY captured_at ASC",
            (since.isoformat(),),
        )
        result = []
        for r in rows:
            dt = _dt(r["captured_at"])
            if dt is not None:
                result.append((dt, float(r[metric] or 0.0)))
        return result

    async def prune_before(self, cutoff: datetime) -> int:
        cur = await self._execute(
            "DELETE FROM metrics_snapshots WHERE captured_at < ?",
            (cutoff.isoformat(),),
        )
        return cur.rowcount
