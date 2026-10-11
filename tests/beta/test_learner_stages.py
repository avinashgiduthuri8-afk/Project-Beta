from datetime import datetime, timezone
from decimal import Decimal

import aiosqlite
import pytest

from core.repository.beta_store import BetaStore
from learner.indian_service import IndianLearnerService


async def add_journal(conn, journal_id, pnl="100", exchange="NSE", timestamp=None):
    await conn.execute(
        """INSERT INTO beta_journal
        (id,position_id,symbol,exchange,strategy,quantity,entry_price,exit_price,net_pnl,exit_timestamp)
        VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (journal_id, f"pos-{journal_id}", "RELIANCE", exchange, "STE", 2, "100", "110", pnl,
         timestamp or datetime.now(timezone.utc).isoformat()),
    )
    await conn.commit()


@pytest.mark.asyncio
async def test_learner_runs_stages_eight_to_ten_and_is_idempotent():
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    store = BetaStore(conn)
    await store.initialize()
    await add_journal(conn, "j1", "100")
    await add_journal(conn, "j2", "-20")
    learner = IndianLearnerService(conn, min_trades=2)
    first = await learner.run_cycle()
    second = await learner.run_cycle()
    assert first["status"] == "COMPLETED"
    assert first["stages"] == [8, 9, 10]
    assert Decimal(first["calibration"]["weight_multiplier"]) <= Decimal("1.20")
    assert second["new_audits"] == 0
    await conn.close()


@pytest.mark.asyncio
async def test_learner_fails_closed_on_malformed_journal():
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    store = BetaStore(conn)
    await store.initialize()
    await add_journal(conn, "bad", "NaN", exchange="NASDAQ", timestamp="missing")
    report = await IndianLearnerService(conn, min_trades=1).run_cycle()
    assert report["status"] == "FAILED"
    assert report["invalid_rows"] == 1
    async with conn.execute("SELECT COUNT(*) FROM beta_learning_audit") as cursor:
        assert (await cursor.fetchone())[0] == 0
    await conn.close()
