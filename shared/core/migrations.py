"""Versioned schema and non-destructive legacy database migration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil

import aiosqlite

from core.database import open_beta_database


SCHEMA_VERSION = 1

OWNED_TABLES = (
    "beta_signals",
    "beta_quotes",
    "beta_paper_account",
    "beta_consumed_signals",
    "beta_positions",
    "beta_journal",
    "beta_learning_audit",
    "beta_scanner_decisions",
    "beta_scanner_rankings",
    "beta_paper_daily_risk",
    "beta_learner_calibrations",
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS beta_schema_version (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), version INTEGER NOT NULL
);
INSERT INTO beta_schema_version(singleton,version) VALUES(1,1)
ON CONFLICT(singleton) DO UPDATE SET version=MAX(version, excluded.version);
CREATE TABLE IF NOT EXISTS beta_signals (
    id TEXT PRIMARY KEY, symbol TEXT NOT NULL, exchange TEXT NOT NULL CHECK(exchange IN ('NSE','BSE')),
    security_id TEXT, strategy TEXT NOT NULL, score INTEGER NOT NULL CHECK(score BETWEEN 0 AND 100),
    price TEXT NOT NULL, market_timestamp TEXT NOT NULL, source TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'QUALIFIED'
);
CREATE TABLE IF NOT EXISTS beta_quotes (
    canonical_symbol TEXT PRIMARY KEY, symbol TEXT NOT NULL, exchange TEXT NOT NULL,
    security_id TEXT, price TEXT NOT NULL, timestamp TEXT NOT NULL, source TEXT NOT NULL,
    generation INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS beta_paper_account (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), cash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS beta_consumed_signals (
    signal_id TEXT PRIMARY KEY, consumed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS beta_positions (
    id TEXT PRIMARY KEY, signal_id TEXT NOT NULL UNIQUE, symbol TEXT NOT NULL, exchange TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK(quantity > 0), entry_price TEXT NOT NULL, charges TEXT NOT NULL,
    charge_breakdown TEXT NOT NULL DEFAULT '{}', opened_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'OPEN'
);
CREATE TABLE IF NOT EXISTS beta_journal (
    id TEXT PRIMARY KEY, position_id TEXT NOT NULL, symbol TEXT NOT NULL, exchange TEXT NOT NULL,
    strategy TEXT NOT NULL, quantity INTEGER NOT NULL, entry_price TEXT NOT NULL,
    exit_price TEXT NOT NULL, net_pnl TEXT NOT NULL, entry_charges TEXT NOT NULL DEFAULT '0',
    exit_charges TEXT NOT NULL DEFAULT '0', charges_json TEXT NOT NULL DEFAULT '{}',
    exit_timestamp TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS beta_learning_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT, journal_id TEXT NOT NULL UNIQUE, strategy TEXT NOT NULL,
    diagnosis TEXT NOT NULL, candidate_multiplier TEXT NOT NULL, promoted INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS beta_scanner_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, cycle_id TEXT NOT NULL, canonical_symbol TEXT NOT NULL,
    stage INTEGER NOT NULL, code TEXT NOT NULL, message TEXT NOT NULL, evidence_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS beta_scanner_rankings (
    cycle_id TEXT NOT NULL, canonical_symbol TEXT NOT NULL, rank INTEGER NOT NULL,
    score TEXT NOT NULL, evidence_json TEXT NOT NULL,
    PRIMARY KEY(cycle_id, canonical_symbol)
);
CREATE TABLE IF NOT EXISTS beta_paper_daily_risk (
    trading_date TEXT PRIMARY KEY, realized_pnl TEXT NOT NULL DEFAULT '0',
    unrealized_pnl TEXT NOT NULL DEFAULT '0', charges TEXT NOT NULL DEFAULT '0',
    consecutive_losses INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'ACTIVE',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS beta_learner_calibrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_json TEXT NOT NULL, evidence_json TEXT NOT NULL,
    status TEXT NOT NULL, created_at TEXT NOT NULL
);
"""


@dataclass(frozen=True, slots=True)
class LegacyDatabasePaths:
    scanner: Path
    paper: Path
    learner: Path


@dataclass(frozen=True, slots=True)
class MigrationReport:
    backups: tuple[Path, ...]
    table_counts: dict[str, int]


async def initialize_beta_schema(conn: aiosqlite.Connection) -> None:
    await conn.executescript(SCHEMA_SQL)
    await conn.commit()


async def _table_names(conn: aiosqlite.Connection, schema: str) -> set[str]:
    async with conn.execute(
        f"SELECT name FROM {schema}.sqlite_master WHERE type='table'"
    ) as cursor:
        return {str(row[0]) for row in await cursor.fetchall()}


async def _columns(
    conn: aiosqlite.Connection, schema: str, table: str
) -> list[str]:
    async with conn.execute(f'PRAGMA {schema}.table_info("{table}")') as cursor:
        return [str(row[1]) for row in await cursor.fetchall()]


async def migrate_legacy_databases(
    target_path: Path,
    legacy_paths: LegacyDatabasePaths,
    backup_dir: Path,
) -> MigrationReport:
    target_path = target_path.resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    backup_dir.mkdir(parents=True, exist_ok=True)
    sources = (
        ("scanner", legacy_paths.scanner.resolve()),
        ("paper", legacy_paths.paper.resolve()),
        ("learner", legacy_paths.learner.resolve()),
    )
    backups: list[Path] = []
    for label, source in sources:
        if not source.exists() or source == target_path:
            continue
        backup = backup_dir / f"{label}-{source.name}"
        shutil.copy2(source, backup)
        backups.append(backup)

    conn = await open_beta_database(str(target_path))
    try:
        await initialize_beta_schema(conn)
        for index, (_, source) in enumerate(sources):
            if not source.exists() or source == target_path:
                continue
            alias = f"legacy_{index}"
            await conn.execute(f"ATTACH DATABASE ? AS {alias}", (str(source),))
            try:
                source_tables = await _table_names(conn, alias)
                await conn.execute("BEGIN IMMEDIATE")
                for table in OWNED_TABLES:
                    if table not in source_tables:
                        continue
                    target_columns = await _columns(conn, "main", table)
                    source_columns = set(await _columns(conn, alias, table))
                    common = [name for name in target_columns if name in source_columns]
                    if not common:
                        continue
                    names = ",".join(f'"{name}"' for name in common)
                    await conn.execute(
                        f'INSERT OR IGNORE INTO main."{table}" ({names}) '
                        f'SELECT {names} FROM {alias}."{table}"'
                    )
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise
            finally:
                await conn.execute(f"DETACH DATABASE {alias}")

        async with conn.execute("PRAGMA foreign_key_check") as cursor:
            violations = await cursor.fetchall()
        if violations:
            raise ValueError(f"Legacy migration failed foreign-key check: {violations!r}")
        counts: dict[str, int] = {}
        for table in OWNED_TABLES:
            async with conn.execute(f'SELECT COUNT(*) FROM "{table}"') as cursor:
                counts[table] = int((await cursor.fetchone())[0])
        return MigrationReport(tuple(backups), counts)
    finally:
        await conn.close()
