"""Small, explicit persistence boundary for Beta's three-service workflow."""

from __future__ import annotations

from decimal import Decimal
import json
from typing import Any

import aiosqlite

from core.migrations import initialize_beta_schema
from market.symbols import StockSymbol


class BetaStore:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self.conn = conn

    async def initialize(self) -> None:
        await initialize_beta_schema(self.conn)

    async def insert_signal_once(
        self,
        signal_id: str,
        symbol: StockSymbol,
        strategy: str,
        score: int,
        price: Decimal,
        market_timestamp: str,
        source: str,
    ) -> bool:
        cursor = await self.conn.execute(
            """INSERT OR IGNORE INTO beta_signals
            (id,symbol,exchange,security_id,strategy,score,price,market_timestamp,source)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (signal_id, symbol.trading_symbol, symbol.exchange, symbol.security_id,
             strategy, int(score), str(price), market_timestamp, source),
        )
        await self.conn.commit()
        return cursor.rowcount == 1

    async def get_signal(self, signal_id: str) -> dict[str, Any] | None:
        async with self.conn.execute(
            "SELECT * FROM beta_signals WHERE id=?", (signal_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def list_qualified_signals(self) -> list[dict[str, Any]]:
        async with self.conn.execute(
            "SELECT * FROM beta_signals WHERE status='QUALIFIED' ORDER BY market_timestamp"
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    async def record_scanner_decision(
        self,
        cycle_id: str,
        canonical_symbol: str,
        stage: int,
        code: str,
        message: str,
        evidence: dict[str, Any],
        created_at: str,
    ) -> None:
        await self.conn.execute(
            """INSERT INTO beta_scanner_decisions
            (cycle_id,canonical_symbol,stage,code,message,evidence_json,created_at)
            VALUES(?,?,?,?,?,?,?)""",
            (cycle_id, canonical_symbol, stage, code, message, json.dumps(evidence, sort_keys=True), created_at),
        )
        await self.conn.commit()

    async def save_rankings(
        self, cycle_id: str, rankings: list[dict[str, Any]]
    ) -> None:
        await self.conn.executemany(
            """INSERT OR REPLACE INTO beta_scanner_rankings
            (cycle_id,canonical_symbol,rank,score,evidence_json) VALUES(?,?,?,?,?)""",
            [
                (
                    cycle_id,
                    row["canonical_symbol"],
                    int(row["rank"]),
                    str(row["score"]),
                    json.dumps(row.get("evidence", {}), sort_keys=True),
                )
                for row in rankings
            ],
        )
        await self.conn.commit()

    async def load_daily_risk_state(self, trading_date: str) -> dict[str, Any] | None:
        async with self.conn.execute(
            "SELECT * FROM beta_paper_daily_risk WHERE trading_date=?", (trading_date,)
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def save_calibration_if_safe(
        self,
        candidate: dict[str, Any],
        evidence: dict[str, Any],
        created_at: str,
    ) -> bool:
        cursor = await self.conn.execute(
            """INSERT INTO beta_learner_calibrations
            (candidate_json,evidence_json,status,created_at) VALUES(?,?,?,?)""",
            (
                json.dumps(candidate, sort_keys=True),
                json.dumps(evidence, sort_keys=True),
                "PROMOTED",
                created_at,
            ),
        )
        await self.conn.commit()
        return cursor.rowcount == 1
