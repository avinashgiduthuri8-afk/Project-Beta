"""
V2 Backtest Repository.

Asynchronous SQLite interface for persisting and querying historical backtest runs,
simulated trade logs, parameter sets, and equity curve metrics.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from core.logging import get_logger

logger = get_logger("core.repository.backtest_repo")


class BacktestRepository:
    """Repository for managing historical backtest runs and simulated trade logs in SQLite."""

    def __init__(self, conn: sqlite3.Connection | aiosqlite.Connection) -> None:
        self._conn = conn

    def _is_async(self) -> bool:
        return isinstance(self._conn, aiosqlite.Connection)

    async def _execute(self, query: str, params: tuple = ()) -> Any:
        if self._is_async():
            return await self._conn.execute(query, params)
        else:
            return self._conn.execute(query, params)

    async def _executemany(self, query: str, params_list: list[tuple]) -> Any:
        if self._is_async():
            return await self._conn.executemany(query, params_list)
        else:
            return self._conn.executemany(query, params_list)

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

    # ── Backtest Runs ─────────────────────────────────────────────────────────

    async def record_run(self, run_data: dict[str, Any]) -> str:
        """Insert a summary backtest run record into SQLite."""
        run_id = str(run_data.get("id") or uuid.uuid4())
        now_str = datetime.now(timezone.utc).isoformat()
        params_raw = run_data.get("parameters")
        params_json = (
            json.dumps(params_raw)
            if isinstance(params_raw, (dict, list))
            else str(params_raw or "{}")
        )

        query = """
        INSERT INTO backtest_runs (
            id, strategy_name, pair, timeframe, start_time, end_time,
            total_trades, win_rate, profit_factor, max_drawdown, sharpe_ratio, cagr, parameters, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            run_id,
            str(run_data["strategy_name"]).upper(),
            str(run_data["pair"]).upper(),
            str(run_data.get("timeframe", "5m")),
            str(run_data["start_time"]),
            str(run_data["end_time"]),
            int(run_data["total_trades"]),
            float(run_data["win_rate"]),
            float(run_data["profit_factor"]),
            float(run_data["max_drawdown"]),
            float(run_data["sharpe_ratio"]),
            float(run_data.get("cagr", 0.0)),
            params_json,
            str(run_data.get("created_at") or now_str),
        )

        await self._execute(query, params)
        if not self._is_async():
            self._conn.commit()

        logger.info(
            "Persisted backtest run %s for strategy %s on %s (Win Rate: %.1f%%, Profit Factor: %.2f)",
            run_id,
            run_data["strategy_name"],
            run_data["pair"],
            float(run_data["win_rate"]),
            float(run_data["profit_factor"]),
        )
        return run_id

    async def record_trades(self, trades: list[dict[str, Any]]) -> None:
        """Bulk insert simulated trade logs for a backtest run."""
        if not trades:
            return

        query = """
        INSERT INTO backtest_trades (
            id, run_id, pair, side, entry_time, exit_time, entry_price, exit_price, quantity,
            gross_pnl, net_pnl, statutory_drag, exit_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params_list = []
        for t in trades:
            trade_id = str(t.get("id") or uuid.uuid4())
            params_list.append(
                (
                    trade_id,
                    str(t["run_id"]),
                    str(t["pair"]).upper(),
                    str(t.get("side", "BUY")).upper(),
                    str(t["entry_time"]),
                    str(t["exit_time"]),
                    float(t["entry_price"]),
                    float(t["exit_price"]),
                    float(t["quantity"]),
                    float(t["gross_pnl"]),
                    float(t["net_pnl"]),
                    float(t["statutory_drag"]),
                    str(t.get("exit_reason", "TP_HIT")),
                )
            )

        await self._executemany(query, params_list)
        if not self._is_async():
            self._conn.commit()

        logger.info("Persisted %d backtest trade log(s)", len(trades))

    async def get_runs(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        """Fetch summary backtest runs ordered by creation time descending."""
        query = "SELECT * FROM backtest_runs ORDER BY created_at DESC LIMIT ? OFFSET ?"
        rows = await self._fetchall(query, (limit, offset))
        for r in rows:
            if r.get("parameters") and isinstance(r["parameters"], str):
                try:
                    r["parameters"] = json.loads(r["parameters"])
                except Exception:
                    pass
        return rows

    async def get_run_detail(self, run_id: str) -> dict[str, Any] | None:
        """Fetch single backtest run summary by run_id."""
        query = "SELECT * FROM backtest_runs WHERE id = ?"
        r = await self._fetchone(query, (run_id,))
        if r and r.get("parameters") and isinstance(r["parameters"], str):
            try:
                r["parameters"] = json.loads(r["parameters"])
            except Exception:
                pass
        return r

    async def get_run_trades(self, run_id: str) -> list[dict[str, Any]]:
        """Fetch all simulated trade records for a specific backtest run."""
        query = "SELECT * FROM backtest_trades WHERE run_id = ? ORDER BY entry_time ASC"
        return await self._fetchall(query, (run_id,))
