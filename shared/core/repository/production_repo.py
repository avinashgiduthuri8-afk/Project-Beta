"""
V2 Production Repository — manages persistence for production runtime state and shadow trade logs.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from core.logging import get_logger

logger = get_logger("core.repository.production_repo")


class ProductionRepository:
    """Async repository for production runtime configuration and shadow execution telemetry."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def get_runtime_state(self) -> dict[str, Any]:
        """Fetch current production deployment mode and kill switch status."""
        async with self._conn.execute("""
            SELECT deployment_mode, is_active, global_kill_switch, updated_at
            FROM production_ops_state
            WHERE id = 1
            """) as cursor:
            row = await cursor.fetchone()
            if row:
                return {
                    "deployment_mode": row[0],
                    "is_active": bool(row[1]),
                    "global_kill_switch": bool(row[2]),
                    "updated_at": row[3],
                }
            return {
                "deployment_mode": "PAPER",
                "is_active": True,
                "global_kill_switch": False,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }

    async def set_deployment_mode(self, mode: str) -> None:
        """Update deployment mode (PAPER, LIVE / LIVE_MICROCASH)."""
        now = datetime.now(timezone.utc).isoformat()
        await self._conn.execute(
            """
            INSERT INTO production_ops_state (id, deployment_mode, is_active, global_kill_switch, updated_at)
            VALUES (1, ?, 1, 0, ?)
            ON CONFLICT(id) DO UPDATE SET
                deployment_mode = excluded.deployment_mode,
                updated_at = excluded.updated_at
            """,
            (mode.upper(), now),
        )
        await self._conn.commit()
        logger.info("Updated production deployment mode to %s", mode.upper())

    async def set_kill_switch(self, tripped: bool) -> None:
        """Set global kill switch state."""
        now = datetime.now(timezone.utc).isoformat()
        await self._conn.execute(
            """
            INSERT INTO production_ops_state (id, deployment_mode, is_active, global_kill_switch, updated_at)
            VALUES (1, 'PAPER', ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                global_kill_switch = excluded.global_kill_switch,
                is_active = CASE WHEN excluded.global_kill_switch = 1 THEN 0 ELSE 1 END,
                updated_at = excluded.updated_at
            """,
            (0 if tripped else 1, 1 if tripped else 0, now),
        )
        await self._conn.commit()
        logger.warning("Production global kill switch set to %s", tripped)

    async def record_shadow_trade_log(
        self,
        bot_name: str,
        pair: str,
        simulated_entry_price: float,
        real_orderbook_entry_price: float,
        slippage_divergence_pct: float,
        log_id: str | None = None,
    ) -> dict[str, Any]:
        """Record shadow vs real orderbook slippage divergence metric."""
        lid = log_id or f"SHD_{uuid.uuid4().hex[:8].upper()}"
        now = datetime.now(timezone.utc).isoformat()

        await self._conn.execute(
            """
            INSERT INTO shadow_trade_logs (
                id, bot_name, pair, simulated_entry_price,
                real_orderbook_entry_price, slippage_divergence_pct, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lid,
                bot_name.upper(),
                pair.upper(),
                simulated_entry_price,
                real_orderbook_entry_price,
                slippage_divergence_pct,
                now,
            ),
        )
        await self._conn.commit()

        return {
            "id": lid,
            "bot_name": bot_name.upper(),
            "pair": pair.upper(),
            "simulated_entry_price": simulated_entry_price,
            "real_orderbook_entry_price": real_orderbook_entry_price,
            "slippage_divergence_pct": slippage_divergence_pct,
            "timestamp": now,
        }

    async def get_shadow_trade_logs(
        self,
        bot_name: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Retrieve recent shadow divergence logs."""
        query = "SELECT id, bot_name, pair, simulated_entry_price, real_orderbook_entry_price, slippage_divergence_pct, timestamp FROM shadow_trade_logs"
        params: list[Any] = []
        if bot_name:
            query += " WHERE bot_name = ?"
            params.append(bot_name.upper())
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        async with self._conn.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [
                {
                    "id": r[0],
                    "bot_name": r[1],
                    "pair": r[2],
                    "simulated_entry_price": r[3],
                    "real_orderbook_entry_price": r[4],
                    "slippage_divergence_pct": r[5],
                    "timestamp": r[6],
                }
                for r in rows
            ]
