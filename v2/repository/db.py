"""
V2 Database — connection management and schema migrations.

Opens a single aiosqlite connection with WAL mode and runs all
pending migrations from v2/repository/migrations/*.sql in version order.

Usage:
    from v2.repository.db import Database

    db = Database("v2/data/alpha_v2.db")
    await db.open()          # run at app startup
    conn = db.connection     # pass to repositories
    await db.close()         # run at app shutdown
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import aiosqlite

from v2.core.exceptions import MigrationError
from v2.core.logging import get_logger

logger = get_logger("v2.repository.db")

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


class Database:
    """Manages the lifecycle of the V2 SQLite connection."""

    def __init__(self, path: str = "v2/data/alpha_v2.db") -> None:
        self._path = path
        self._conn: Optional[aiosqlite.Connection] = None

    @property
    def connection(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise MigrationError("Database.open() has not been called.")
        return self._conn

    async def open(self) -> None:
        """Open the connection and apply any pending migrations."""
        # Ensure the data directory exists
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row

        # WAL mode + foreign keys
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.commit()

        await self._run_migrations()
        logger.info("Database opened", extra={"path": self._path})

    async def close(self) -> None:
        """Flush WAL and close the connection."""
        if self._conn:
            await self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            await self._conn.close()
            self._conn = None
            logger.info("Database closed")

    # ── Migration runner ──────────────────────────────────────────────────────

    async def _applied_versions(self) -> set[int]:
        """Return set of already-applied migration version numbers."""
        try:
            async with self._conn.execute(
                "SELECT version FROM schema_version"
            ) as cur:
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
            logger.info("Applying migration", extra={"version": version, "file": sql_file.name})
            try:
                sql = sql_file.read_text()
                # Split on semicolons, skip empty statements
                statements = [s.strip() for s in sql.split(";") if s.strip()]
                for stmt in statements:
                    await self._conn.execute(stmt)
                await self._conn.commit()
                logger.info("Migration applied", extra={"version": version})
            except Exception as exc:
                raise MigrationError(
                    f"Migration {version} ({sql_file.name}) failed: {exc}"
                ) from exc
