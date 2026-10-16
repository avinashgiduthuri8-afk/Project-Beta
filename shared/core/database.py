"""Canonical SQLite connection lifecycle for Project Beta."""

from __future__ import annotations

from pathlib import Path

import aiosqlite


async def open_beta_database(
    path: str, *, read_only: bool = False
) -> aiosqlite.Connection:
    resolved = Path(path).resolve()
    if read_only:
        conn = await aiosqlite.connect(
            f"file:{resolved.as_posix()}?mode=ro", uri=True, timeout=5.0
        )
    else:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(resolved, timeout=5.0)
        await conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys=ON")
    await conn.execute("PRAGMA busy_timeout=5000")
    return conn
