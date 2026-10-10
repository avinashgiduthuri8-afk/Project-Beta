"""
V2 CandleRepository — persistence for historical market candles.
"""

from __future__ import annotations

from typing import Any

from core.exceptions import StorageError
from core.logging import get_logger

from .base import BaseRepository

logger = get_logger("core.repository.candle_repo")


class CandleRepository(BaseRepository):
    """Manages SQLite storage for historical market candles."""

    async def get_recent_candles(
        self,
        pair: str,
        timeframe: str,
        limit: int = 120,
        max_timestamp_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        Get the most recent candles for a pair and timeframe, up to an optional max timestamp (for backtesting).
        Returns a list of dicts ordered chronologically (ascending timestamp).
        """
        if max_timestamp_ms is not None:
            query = """
                SELECT pair, timeframe, timestamp, open, high, low, close, volume
                FROM market_candles
                WHERE pair = ? AND timeframe = ? AND timestamp <= ?
                ORDER BY timestamp DESC
                LIMIT ?
            """
            params = (pair.upper(), timeframe, max_timestamp_ms, limit)
        else:
            query = """
                SELECT pair, timeframe, timestamp, open, high, low, close, volume
                FROM market_candles
                WHERE pair = ? AND timeframe = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """
            params = (pair.upper(), timeframe, limit)

        try:
            async with self._conn.execute(query, params) as cursor:
                rows = await cursor.fetchall()

            # Convert sqlite rows to list of dicts, and reverse to be ascending (chronological)
            candles = []
            for r in reversed(rows):
                candles.append(
                    {
                        "pair": r["pair"],
                        "timeframe": r["timeframe"],
                        "timestamp": r["timestamp"],
                        "open": r["open"],
                        "high": r["high"],
                        "low": r["low"],
                        "close": r["close"],
                        "volume": r["volume"],
                    }
                )
            return candles
        except Exception as exc:
            raise StorageError(f"Failed to fetch candles: {exc}") from exc

    async def get_candles_range(
        self,
        pair: str,
        timeframe: str,
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int = 10000,
    ) -> list[dict[str, Any]]:
        """
        Get candles for a pair and timeframe within an optional timestamp range.
        Returns a list of dicts ordered chronologically (ascending timestamp).
        """
        clauses = ["pair = ?", "timeframe = ?"]
        params: list[Any] = [pair.upper(), timeframe]

        if start_time is not None:
            clauses.append("timestamp >= ?")
            params.append(start_time)
        if end_time is not None:
            clauses.append("timestamp <= ?")
            params.append(end_time)

        where_clause = " AND ".join(clauses)
        query = f"""
            SELECT pair, timeframe, timestamp, open, high, low, close, volume
            FROM market_candles
            WHERE {where_clause}
            ORDER BY timestamp ASC
            LIMIT ?
        """
        params.append(limit)

        try:
            async with self._conn.execute(query, params) as cursor:
                rows = await cursor.fetchall()

            candles = []
            for r in rows:
                candles.append(
                    {
                        "pair": r["pair"],
                        "timeframe": r["timeframe"],
                        "timestamp": r["timestamp"],
                        "open": r["open"],
                        "high": r["high"],
                        "low": r["low"],
                        "close": r["close"],
                        "volume": r["volume"],
                    }
                )
            return candles
        except Exception as exc:
            raise StorageError(f"Failed to fetch candles range: {exc}") from exc

    async def upsert_candles(self, candles_list: list[dict[str, Any]]) -> None:
        """
        Insert or replace a list of candles in the database.
        Idempotent bulk write.
        """
        if not candles_list:
            return

        query = """
            INSERT OR REPLACE INTO market_candles (pair, timeframe, timestamp, open, high, low, close, volume)
            VALUES (:pair, :timeframe, :timestamp, :open, :high, :low, :close, :volume)
        """

        # Ensure correct types and capitalization, filtering out corrupted / negative candles
        formatted = []
        for c in candles_list:
            o = float(c.get("open", c.get("o", 0.0)))
            h = float(c.get("high", c.get("h", 0.0)))
            l = float(c.get("low", c.get("l", 0.0)))
            close_px = float(c.get("close", c.get("c", 0.0)))
            v = float(c.get("volume", c.get("v", 0.0)))

            if o <= 0 or h <= 0 or l <= 0 or close_px <= 0:
                continue
            if h < l or h < o or h < close_px or l > o or l > close_px:
                continue

            formatted.append(
                {
                    "pair": str(c["pair"]).upper(),
                    "timeframe": str(c["timeframe"]),
                    "timestamp": int(c["timestamp"]),
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": close_px,
                    "volume": v,
                }
            )

        try:
            await self._conn.executemany(query, formatted)
            await self._conn.commit()
        except Exception as exc:
            raise StorageError(f"Failed to upsert candles: {exc}") from exc
