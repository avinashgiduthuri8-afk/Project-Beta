"""
V2 SignalRepository — persistence for scanner signals.
"""

from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite

from core.logging import get_logger
from core.types import MarketState, OppType, Priority, RiskLevel, Signal

from .base import BaseRepository

logger = get_logger("core.repository.signal_repo")

_ISO = "%Y-%m-%dT%H:%M:%S.%f+00:00"


def _dt(s: str | None) -> datetime | None:
    if s is None:
        return None
    for fmt in (_ISO, "%Y-%m-%dT%H:%M:%S+00:00", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def _row_to_signal(row: aiosqlite.Row) -> Signal:
    d = dict(row)
    return Signal(
        id=d["id"],
        coin=d["coin"],
        pair=d["pair"],
        market_state=MarketState(d["market_state"]),
        opportunity_type=OppType(d["opportunity_type"]),
        priority=Priority(d["priority"]),
        risk_level=RiskLevel(d["risk_level"]),
        score=d["score"],
        confidence=d["confidence"],
        coin_class=d.get("coin_class"),
        mtf_alignment=bool(d["mtf_alignment"]),
        generated_at=_dt(d["generated_at"]),
        expires_at=_dt(d["expires_at"]),
        source_bot=d.get("source_bot", "scanner_v1"),
        raw_payload=BaseRepository._loads(d.get("raw_payload")) or {},
    )


class SignalRepository(BaseRepository):
    async def insert(self, signal: Signal) -> str:
        """Persist a new signal. Returns the signal id."""
        await self._execute(
            """
            INSERT OR IGNORE INTO signals
            (id, coin, pair, market_state, opportunity_type, priority,
             risk_level, score, confidence, coin_class, mtf_alignment,
             generated_at, expires_at, source_bot, raw_payload)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                signal.id,
                signal.coin,
                signal.pair,
                signal.market_state.value,
                signal.opportunity_type.value,
                signal.priority.value,
                signal.risk_level.value,
                signal.score,
                signal.confidence,
                signal.coin_class,
                int(signal.mtf_alignment),
                signal.generated_at.isoformat(),
                signal.expires_at.isoformat(),
                signal.source_bot,
                self._dumps(signal.raw_payload),
            ),
        )
        return signal.id

    async def mark_expired(self, signal_id: str, reason: str = "TTL") -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self._execute(
            "UPDATE signals SET expired_at=?, expiry_reason=? WHERE id=?",
            (now, reason, signal_id),
        )

    async def get_live(
        self,
        priority_gte: Priority | None = None,
        grace_period_seconds: float = 0.0,
    ) -> list[Signal]:
        """Return signals that have not yet expired, optionally allowing a grace period for recovery."""
        if grace_period_seconds > 0:
            cutoff = datetime.fromtimestamp(
                datetime.now(timezone.utc).timestamp() - grace_period_seconds,
                tz=timezone.utc,
            ).isoformat()
        else:
            cutoff = datetime.now(timezone.utc).isoformat()

        rows = await self._fetchall(
            "SELECT * FROM signals WHERE expires_at > ? AND expired_at IS NULL "
            "ORDER BY score DESC",
            (cutoff,),
        )
        signals = [_row_to_signal(r) for r in rows]
        if priority_gte is not None:
            signals = [s for s in signals if s.priority.gte(priority_gte)]
        return signals

    async def get_active(self, limit: int = 5) -> list[Signal]:
        """Alias for get_live for watchdog and supervisor compatibility."""
        signals = await self.get_live()
        return signals[:limit]

    async def get_by_id(self, signal_id: str) -> Signal | None:
        row = await self._fetchone("SELECT * FROM signals WHERE id=?", (signal_id,))
        return _row_to_signal(row) if row else None

    async def get_by_coin(self, coin: str, limit: int = 20) -> list[Signal]:
        rows = await self._fetchall(
            "SELECT * FROM signals WHERE coin=? ORDER BY generated_at DESC LIMIT ?",
            (coin, limit),
        )
        return [_row_to_signal(r) for r in rows]

    async def get_history(self, since: datetime, limit: int = 200) -> list[Signal]:
        rows = await self._fetchall(
            "SELECT * FROM signals WHERE generated_at >= ? "
            "ORDER BY generated_at DESC LIMIT ?",
            (since.isoformat(), limit),
        )
        return [_row_to_signal(r) for r in rows]

    async def count_by_priority(self, since: datetime) -> dict[str, int]:
        rows = await self._fetchall(
            "SELECT priority, COUNT(*) as n FROM signals "
            "WHERE generated_at >= ? GROUP BY priority",
            (since.isoformat(),),
        )
        return {r["priority"]: r["n"] for r in rows}

    async def exists(self, signal_id: str) -> bool:
        row = await self._fetchone("SELECT 1 FROM signals WHERE id=?", (signal_id,))
        return row is not None

    async def get_recent(self, limit: int = 100) -> list[Signal]:
        """Return the most recently generated signals ordered by generated_at DESC."""
        rows = await self._fetchall(
            "SELECT * FROM signals ORDER BY generated_at DESC LIMIT ?",
            (limit,),
        )
        return [_row_to_signal(r) for r in rows]

    async def get_total_count(self) -> int:
        """Return total number of signals in repository."""
        row = await self._fetchone("SELECT COUNT(*) FROM signals")
        return int(row[0]) if row else 0
