import asyncio
from hashlib import sha256
from pathlib import Path

import aiosqlite
import pytest

from core.database import open_beta_database
from core.migrations import (
    LegacyDatabasePaths,
    initialize_beta_schema,
    migrate_legacy_databases,
)
from core.repository.beta_store import BetaStore
from market.symbols import StockSymbol


async def _pragma(conn, name):
    async with conn.execute(f"PRAGMA {name}") as cursor:
        return (await cursor.fetchone())[0]


@pytest.mark.asyncio
async def test_shared_connections_enable_integrity_and_survive_concurrent_writes(tmp_path):
    path = tmp_path / "beta.db"
    connections = [await open_beta_database(str(path)) for _ in range(3)]
    try:
        await initialize_beta_schema(connections[0])
        foreign_keys = await asyncio.gather(*(_pragma(conn, "foreign_keys") for conn in connections))
        journal_modes = await asyncio.gather(*(_pragma(conn, "journal_mode") for conn in connections))
        assert foreign_keys == [1, 1, 1]
        assert [mode.lower() for mode in journal_modes] == ["wal", "wal", "wal"]

        stores = [BetaStore(conn) for conn in connections]
        symbol = StockSymbol.parse("NSE:TEST")
        results = await asyncio.gather(*[
            store.insert_signal_once(
                "sig-shared", symbol, "STE", 90, 10, "2026-10-16T04:00:00+00:00", "TEST"
            )
            for store in stores
        ])
        assert results.count(True) == 1
    finally:
        await asyncio.gather(*(conn.close() for conn in connections))

    reopened = await open_beta_database(str(path))
    try:
        assert await BetaStore(reopened).get_signal("sig-shared") is not None
    finally:
        await reopened.close()


async def _legacy_database(path: Path, signal_id: str) -> None:
    conn = await aiosqlite.connect(path)
    conn.row_factory = aiosqlite.Row
    store = BetaStore(conn)
    await store.initialize()
    await store.insert_signal_once(
        signal_id,
        StockSymbol.parse("NSE:TEST"),
        "STE",
        90,
        10,
        "2026-10-16T04:00:00+00:00",
        "TEST",
    )
    await conn.close()


@pytest.mark.asyncio
async def test_legacy_migration_is_non_destructive_and_idempotent(tmp_path):
    scanner = tmp_path / "scanner.db"
    paper = tmp_path / "paper.db"
    learner = tmp_path / "learner.db"
    await _legacy_database(scanner, "sig-scanner")
    await _legacy_database(paper, "sig-paper")
    await _legacy_database(learner, "sig-learner")
    legacy = LegacyDatabasePaths(scanner=scanner, paper=paper, learner=learner)
    hashes = {path: sha256(path.read_bytes()).hexdigest() for path in (scanner, paper, learner)}

    target = tmp_path / "shared" / "project_beta.db"
    backup_dir = tmp_path / "backups"
    first = await migrate_legacy_databases(target, legacy, backup_dir)
    second = await migrate_legacy_databases(target, legacy, backup_dir)

    assert first.table_counts["beta_signals"] == 3
    assert second.table_counts["beta_signals"] == 3
    assert len(first.backups) == 3
    assert all(path.exists() for path in first.backups)
    assert hashes == {path: sha256(path.read_bytes()).hexdigest() for path in hashes}

    conn = await open_beta_database(str(target))
    try:
        async with conn.execute("PRAGMA foreign_key_check") as cursor:
            assert await cursor.fetchall() == []
        async with conn.execute("SELECT version FROM beta_schema_version") as cursor:
            assert (await cursor.fetchone())[0] >= 1
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_service_owned_repository_methods_round_trip(tmp_path):
    conn = await open_beta_database(str(tmp_path / "beta.db"))
    store = BetaStore(conn)
    await store.initialize()
    try:
        await store.record_scanner_decision(
            "cycle-1", "NSE:TEST", 2, "PENNY_STOCK", "strict checks required",
            {"price": "0.25"}, "2026-10-16T04:00:00+00:00",
        )
        await store.save_rankings(
            "cycle-1", [{"canonical_symbol": "NSE:TEST", "rank": 1, "score": "91", "evidence": {"trend": "UP"}}]
        )
        assert await store.load_daily_risk_state("2026-10-16") is None
        assert await store.save_calibration_if_safe(
            {"confluence_threshold": "85"}, {"walk_forward": "PASS"},
            "2026-10-16T04:00:00+00:00",
        ) is True
        async with conn.execute("SELECT COUNT(*) FROM beta_scanner_decisions") as cursor:
            assert (await cursor.fetchone())[0] == 1
        async with conn.execute("SELECT COUNT(*) FROM beta_scanner_rankings") as cursor:
            assert (await cursor.fetchone())[0] == 1
        async with conn.execute("SELECT status FROM beta_learner_calibrations") as cursor:
            assert (await cursor.fetchone())[0] == "PROMOTED"
    finally:
        await conn.close()
