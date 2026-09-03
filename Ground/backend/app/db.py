import asyncio
from pathlib import Path
from typing import Any, Iterable

import aiosqlite


SCHEMA = """
CREATE TABLE IF NOT EXISTS aircraft (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    alias TEXT,
    static_ip TEXT,
    last_ip TEXT,
    claim_state TEXT NOT NULL DEFAULT 'unclaimed',
    channel TEXT NOT NULL DEFAULT 'v1',
    ssh_host TEXT,
    ssh_user TEXT,
    ssh_key_path TEXT,
    registered_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS claims (
    claim_id TEXT PRIMARY KEY,
    aircraft_id TEXT NOT NULL,
    code TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    redeemed_at REAL,
    redeemed_ip TEXT
);
CREATE TABLE IF NOT EXISTS "params" (
    aircraft_id TEXT PRIMARY KEY,
    data TEXT NOT NULL DEFAULT '{}',
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    version INTEGER NOT NULL,
    load_addr INTEGER NOT NULL,
    entry INTEGER NOT NULL,
    crc32 INTEGER NOT NULL,
    uploaded_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS flash_jobs (
    id TEXT PRIMARY KEY,
    aircraft_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    status TEXT NOT NULL,
    phase TEXT NOT NULL DEFAULT 'queued',
    percent INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    log_tail TEXT NOT NULL DEFAULT '',
    started_at REAL,
    finished_at REAL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    aircraft_id TEXT,
    level TEXT NOT NULL,
    code TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    data TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_flash_jobs_aircraft ON flash_jobs(aircraft_id, started_at);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
"""


def _row(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class Database:
    def __init__(self, path: str):
        self.path = Path(path)
        self._conn: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    async def init(self) -> None:
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self.path))
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA)
        await self._ensure_aircraft_columns()
        await self._conn.commit()

    async def _ensure_aircraft_columns(self) -> None:
        cursor = await self._conn.execute("PRAGMA table_info(aircraft)")
        rows = await cursor.fetchall()
        await cursor.close()
        existing = {row[1] for row in rows}
        additions = {
            "alias": "TEXT",
            "static_ip": "TEXT",
            "last_ip": "TEXT",
            "claim_state": "TEXT NOT NULL DEFAULT 'unclaimed'",
            "channel": "TEXT NOT NULL DEFAULT 'v1'",
        }
        for column, definition in additions.items():
            if column not in existing:
                await self._conn.execute(
                    f"ALTER TABLE aircraft ADD COLUMN {column} {definition}"
                )

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        assert self._conn is not None
        async with self._write_lock:
            cursor = await self._conn.execute(sql, tuple(params))
            await self._conn.commit()
            return cursor.lastrowid or 0

    async def query_all(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        assert self._conn is not None
        cursor = await self._conn.execute(sql, tuple(params))
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(r) for r in rows]

    async def query_one(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        rows = await self.query_all(sql, params)
        return rows[0] if rows else None
