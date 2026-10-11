"""Fail-closed post-trade learning for Beta stages 8–10."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json

import aiosqlite


class IndianLearnerService:
    ALLOWED_STRATEGIES = {"STE", "HDA", "VCP", "BBS", "MTB", "NR7", "PPA", "MRB"}

    def __init__(self, conn: aiosqlite.Connection, min_trades: int = 3) -> None:
        self.conn = conn
        self.min_trades = max(1, min_trades)

    @staticmethod
    def _validate(row: dict) -> tuple[bool, Decimal | None]:
        if row.get("exchange") not in {"NSE", "BSE"} or row.get("strategy") not in IndianLearnerService.ALLOWED_STRATEGIES:
            return False, None
        try:
            pnl = Decimal(str(row["net_pnl"]))
            timestamp = datetime.fromisoformat(str(row["exit_timestamp"]))
        except (InvalidOperation, ValueError, TypeError, KeyError):
            return False, None
        if not pnl.is_finite() or timestamp.tzinfo is None:
            return False, None
        return True, pnl

    async def run_cycle(self, limit: int = 100) -> dict:
        async with self.conn.execute("SELECT * FROM beta_journal ORDER BY exit_timestamp LIMIT ?", (int(limit),)) as cursor:
            rows = [dict(row) for row in await cursor.fetchall()]
        validated = [(row, self._validate(row)) for row in rows]
        invalid = sum(1 for _, (valid, _) in validated if not valid)
        if invalid:
            return {"status": "FAILED", "stages": [8], "invalid_rows": invalid, "new_audits": 0}
        if len(rows) < self.min_trades:
            return {"status": "INSUFFICIENT_DATA", "stages": [8], "trade_count": len(rows), "invalid_rows": 0, "new_audits": 0}

        pnls = [pnl for _, (_, pnl) in validated if pnl is not None]
        wins = sum(1 for pnl in pnls if pnl > 0)
        win_rate = Decimal(wins) / Decimal(len(pnls))
        multiplier = max(Decimal("0.80"), min(Decimal("1.20"), Decimal("1") + (win_rate - Decimal("0.5")) * Decimal("0.2")))
        threshold = max(Decimal("75"), min(Decimal("95"), Decimal("90") - win_rate * Decimal("10")))
        calibration = {"strategy": "STE", "weight_multiplier": str(multiplier), "confluence_threshold": str(threshold)}

        inserted = 0
        now = datetime.now(timezone.utc).isoformat()
        for row, (_, pnl) in validated:
            diagnosis = "PROFITABLE_SETUP" if pnl and pnl > 0 else "LOSS_REVIEW"
            cursor = await self.conn.execute(
                """INSERT OR IGNORE INTO beta_learning_audit
                (journal_id,strategy,diagnosis,candidate_multiplier,promoted,created_at)
                VALUES(?,?,?,?,?,?)""",
                (row["id"], row["strategy"], diagnosis, json.dumps(calibration), 1, now),
            )
            inserted += max(0, cursor.rowcount)
        await self.conn.commit()
        return {"status": "COMPLETED", "stages": [8, 9, 10], "trade_count": len(rows), "invalid_rows": 0, "new_audits": inserted, "calibration": calibration}
