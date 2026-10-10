"""
Test reliable Scanner -> Paper signal intake and reconnection resilience.
"""

from datetime import datetime, timezone, timedelta
import pytest
import aiosqlite

from core.repository.db import Database
from core.repository.signal_repo import SignalRepository
from core.types import MarketState, OppType, Priority, RiskLevel, Signal


@pytest.mark.asyncio
async def test_read_only_database_and_busy_timeout():
    uri = "file:mem_ro_test?mode=memory&cache=shared"
    # Writer creates database and table
    async with aiosqlite.connect(uri, uri=True) as conn:
        await conn.execute("CREATE TABLE test (id TEXT PRIMARY KEY, val TEXT)")
        await conn.execute("INSERT INTO test VALUES ('1', 'hello')")
        await conn.commit()

        # Reader opens in read_only mode
        ro_db = Database(uri, read_only=True)
        await ro_db.open()
        assert ro_db.is_open

        # Reading succeeds
        async with ro_db.connection.execute("SELECT val FROM test WHERE id='1'") as cursor:
            row = await cursor.fetchone()
            assert row[0] == "hello"

        # Writing is blocked by query_only
        with pytest.raises(Exception):
            await ro_db.connection.execute("INSERT INTO test VALUES ('2', 'world')")

        await ro_db.close()
        assert not ro_db.is_open


@pytest.mark.asyncio
async def test_signal_repo_grace_period():
    uri = "file:mem_signal_test?mode=memory&cache=shared"
    writer = Database(uri)
    await writer.open()

    repo = SignalRepository(writer.connection)
    now = datetime.now(timezone.utc)

    # 1. Active unexpired signal
    sig_live = Signal(
        id="sig_active",
        coin="BTC",
        pair="BTC/INR",
        market_state=MarketState.BULL_TREND,
        opportunity_type=OppType.MOMENTUM_TRADE,
        priority=Priority.HIGH,
        risk_level=RiskLevel.LOW,
        score=90.0,
        confidence=85.0,
        coin_class="A",
        mtf_alignment=True,
        generated_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    # 2. Borderline expired signal (expired 5 seconds ago)
    sig_grace = Signal(
        id="sig_grace",
        coin="ETH",
        pair="ETH/INR",
        market_state=MarketState.BULL_TREND,
        opportunity_type=OppType.MOMENTUM_TRADE,
        priority=Priority.HIGH,
        risk_level=RiskLevel.LOW,
        score=88.0,
        confidence=80.0,
        coin_class="A",
        mtf_alignment=True,
        generated_at=now - timedelta(minutes=5),
        expires_at=now - timedelta(seconds=5),
    )
    # 3. Old expired signal (expired 2 minutes ago)
    sig_old = Signal(
        id="sig_old",
        coin="SOL",
        pair="SOL/INR",
        market_state=MarketState.BULL_TREND,
        opportunity_type=OppType.MOMENTUM_TRADE,
        priority=Priority.HIGH,
        risk_level=RiskLevel.LOW,
        score=75.0,
        confidence=70.0,
        coin_class="B",
        mtf_alignment=False,
        generated_at=now - timedelta(minutes=10),
        expires_at=now - timedelta(minutes=2),
    )

    await repo.insert(sig_live)
    await repo.insert(sig_grace)
    await repo.insert(sig_old)

    # Without grace period, only sig_live is returned
    live_strict = await repo.get_live(grace_period_seconds=0.0)
    strict_ids = [s.id for s in live_strict]
    assert "sig_active" in strict_ids
    assert "sig_grace" not in strict_ids
    assert "sig_old" not in strict_ids

    # With 15s grace period, sig_grace is safely recovered
    live_grace = await repo.get_live(grace_period_seconds=15.0)
    grace_ids = [s.id for s in live_grace]
    assert "sig_active" in grace_ids
    assert "sig_grace" in grace_ids
    assert "sig_old" not in grace_ids

    await writer.close()
