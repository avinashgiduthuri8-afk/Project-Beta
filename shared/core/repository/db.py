"""
PROJECT-ALPHA Database — connection management and schema migrations.

Opens a single aiosqlite connection with WAL mode and runs all
pending migrations from migrations/*.sql in version order.

Canonical path: data/project_alpha.db
Legacy fallback: v2/data/alpha_v2.db

Usage:
    from core.repository.db import Database

    db = Database("data/project_alpha.db")
    await db.open()          # run at app startup
    conn = db.connection     # pass to repositories
    await db.close()         # run at app shutdown
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import aiosqlite

from core.exceptions import MigrationError
from core.logging import get_logger

logger = get_logger("repository.db")

_LOCAL_MIGRATIONS = Path(__file__).parent / "migrations"
_FALLBACK_MIGRATIONS = Path("v2/repository/migrations")
_MIGRATIONS_DIR = (
    _LOCAL_MIGRATIONS if _LOCAL_MIGRATIONS.exists() else _FALLBACK_MIGRATIONS
)

CANONICAL_DB_PATH = "data/project_alpha.db"
LEGACY_DB_PATH = "v2/data/alpha_v2.db"


def migrate_database_if_needed(
    canonical_path: str = CANONICAL_DB_PATH,
    legacy_path: str = LEGACY_DB_PATH,
) -> str:
    """
    Safely migrate an existing SQLite database from legacy path to canonical path.
    Preserves existing files (.db, .db-wal, .db-shm) at legacy location without deletion.
    """
    target = Path(canonical_path)
    legacy = Path(legacy_path)

    # 1. If canonical database already exists, use it
    if target.exists():
        return str(target)

    # 2. If legacy database exists, safely copy to canonical location
    if legacy.exists():
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(legacy, target)
            logger.info("Migrated SQLite database from %s to %s", legacy, target)

            # Copy WAL file if present
            legacy_wal = legacy.with_name(legacy.name + "-wal")
            if legacy_wal.exists():
                target_wal = target.with_name(target.name + "-wal")
                shutil.copy2(legacy_wal, target_wal)
                logger.info(
                    "Migrated SQLite WAL file from %s to %s", legacy_wal, target_wal
                )

            # Copy SHM file if present
            legacy_shm = legacy.with_name(legacy.name + "-shm")
            if legacy_shm.exists():
                target_shm = target.with_name(target.name + "-shm")
                shutil.copy2(legacy_shm, target_shm)
                logger.info(
                    "Migrated SQLite SHM file from %s to %s", legacy_shm, target_shm
                )

            return str(target)
        except Exception as exc:
            logger.warning(
                "Failed to copy legacy database from %s to %s: %s. Falling back to legacy path.",
                legacy,
                target,
                exc,
            )
            return str(legacy)

    # 3. Neither exists; ensure directory and use canonical target
    target.parent.mkdir(parents=True, exist_ok=True)
    return str(target)


class Database:
    """Manages the lifecycle of the SQLite connection."""

    def __init__(self, path: str | None = None, read_only: bool = False) -> None:
        if path is None:
            path = CANONICAL_DB_PATH
        self._path = path
        self._read_only = read_only
        self._conn: aiosqlite.Connection | None = None

    @property
    def path(self) -> str:
        return self._path

    @property
    def is_open(self) -> bool:
        return self._conn is not None

    @property
    def connection(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise MigrationError("Database.open() has not been called.")
        return self._conn

    async def open(self) -> None:
        """Open the connection and apply any pending migrations (if not read_only)."""
        # Handle migration / fallback if pointing to canonical or legacy path
        if not self._read_only:
            if self._path == CANONICAL_DB_PATH:
                self._path = migrate_database_if_needed(self._path, LEGACY_DB_PATH)
            elif self._path == LEGACY_DB_PATH:
                if not Path(self._path).exists() and Path(CANONICAL_DB_PATH).exists():
                    self._path = CANONICAL_DB_PATH

            # Ensure the data directory exists
            db_p = Path(self._path)
            if db_p.parent and str(db_p.parent) not in ("", "."):
                db_p.parent.mkdir(parents=True, exist_ok=True)

        is_uri = str(self._path).startswith("file:")
        self._conn = await aiosqlite.connect(self._path, uri=is_uri, timeout=10.0)
        self._conn.row_factory = aiosqlite.Row

        # Busy timeout + WAL / read_only configuration
        await self._conn.execute("PRAGMA busy_timeout=5000")
        if self._read_only:
            await self._conn.execute("PRAGMA query_only=ON")
        else:
            await self._conn.execute("PRAGMA journal_mode=WAL")
            await self._conn.execute("PRAGMA foreign_keys=ON")
            await self._conn.commit()
            await self._run_migrations()

        logger.info("Database opened", extra={"path": self._path, "read_only": self._read_only})

    async def close(self) -> None:
        """Flush WAL and close the connection."""
        if self._conn:
            try:
                if not self._read_only:
                    await self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:
                pass
            finally:
                await self._conn.close()
                self._conn = None
                logger.info("Database closed")

    # ── Migration runner ──────────────────────────────────────────────────────

    async def _applied_versions(self) -> set[int]:
        """Return set of already-applied migration version numbers."""
        try:
            async with self._conn.execute("SELECT version FROM schema_version") as cur:
                rows = await cur.fetchall()
            return {row[0] for row in rows}
        except Exception:
            # Table does not exist yet — first run
            return set()

    async def _run_migrations(self) -> None:
        """Apply all pending *.sql migration files in version order."""
        if not _MIGRATIONS_DIR.exists():
            return

        applied = await self._applied_versions()
        pending: list[tuple[int, Path]] = []

        for sql_file in sorted(_MIGRATIONS_DIR.glob("*.sql")):
            match = re.match(r"^(\d+)_", sql_file.name)
            if not match:
                continue
            version = int(match.group(1))
            if version not in applied:
                pending.append((version, sql_file))

        if not pending:
            logger.info("Migrations: all up to date")
            return

        for version, sql_file in sorted(pending):
            logger.info(
                "Applying migration", extra={"version": version, "file": sql_file.name}
            )
            try:
                sql = sql_file.read_text(encoding="utf-8").lstrip("\ufeff")
                # Split on semicolons, skip empty statements
                statements = [s.strip() for s in sql.split(";") if s.strip()]
                for stmt in statements:
                    await self._conn.execute(stmt)
                # Ensure migration version is recorded in schema_version
                await self._conn.execute(
                    "INSERT OR IGNORE INTO schema_version (version, applied_at, description) VALUES (?, datetime('now'), ?)",
                    (version, sql_file.name),
                )
                await self._conn.commit()
                logger.info("Migration applied", extra={"version": version})
            except Exception as exc:
                raise MigrationError(
                    f"Migration {version} ({sql_file.name}) failed: {exc}"
                ) from exc
