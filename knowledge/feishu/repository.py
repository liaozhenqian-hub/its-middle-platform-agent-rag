from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite


_ERROR_TYPE_PATTERN = re.compile(r"[^A-Za-z0-9_.-]+")


class FeishuEventRepository:
    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)

    async def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        async with self._connect() as database:
            await database.execute("PRAGMA journal_mode=WAL")
            await database.execute(
                """
                CREATE TABLE IF NOT EXISTS feishu_schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            await database.execute(
                """
                CREATE TABLE IF NOT EXISTS feishu_events (
                    event_id TEXT PRIMARY KEY,
                    message_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('processing','completed','failed')),
                    attempt INTEGER NOT NULL CHECK(attempt BETWEEN 1 AND 2),
                    error_type TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await database.execute(
                "INSERT OR IGNORE INTO feishu_schema_migrations(version, applied_at) VALUES(1, ?)",
                (self._now(),),
            )
            await database.execute(
                """
                UPDATE feishu_events
                SET status='failed', error_type='ServiceRestart', updated_at=?
                WHERE status='processing'
                """,
                (self._now(),),
            )
            await database.commit()

    async def claim(self, event_id: str, message_id: str, chat_id: str) -> bool:
        event_id = event_id.strip()
        message_id = message_id.strip()
        chat_id = chat_id.strip()
        if not event_id or not message_id or not chat_id:
            raise ValueError("event_id, message_id and chat_id are required")
        async with self._connect() as database:
            await database.execute("BEGIN IMMEDIATE")
            row = await (
                await database.execute(
                    "SELECT status, attempt FROM feishu_events WHERE event_id=?",
                    (event_id,),
                )
            ).fetchone()
            now = self._now()
            claimed = False
            if row is None:
                await database.execute(
                    """
                    INSERT INTO feishu_events(
                        event_id, message_id, chat_id, status, attempt,
                        error_type, created_at, updated_at
                    ) VALUES(?, ?, ?, 'processing', 1, NULL, ?, ?)
                    """,
                    (event_id, message_id, chat_id, now, now),
                )
                claimed = True
            elif row["status"] == "failed" and int(row["attempt"]) < 2:
                await database.execute(
                    """
                    UPDATE feishu_events
                    SET status='processing', attempt=attempt+1, error_type=NULL,
                        message_id=?, chat_id=?, updated_at=?
                    WHERE event_id=?
                    """,
                    (message_id, chat_id, now, event_id),
                )
                claimed = True
            await database.commit()
            return claimed

    async def complete(self, event_id: str) -> None:
        async with self._connect() as database:
            await database.execute(
                """
                UPDATE feishu_events
                SET status='completed', error_type=NULL, updated_at=?
                WHERE event_id=? AND status='processing'
                """,
                (self._now(), event_id),
            )
            await database.commit()

    async def fail(self, event_id: str, error_type: str) -> None:
        sanitized = _ERROR_TYPE_PATTERN.sub("", str(error_type))[:100] or "Error"
        async with self._connect() as database:
            await database.execute(
                """
                UPDATE feishu_events
                SET status='failed', error_type=?, updated_at=?
                WHERE event_id=? AND status='processing'
                """,
                (sanitized, self._now(), event_id),
            )
            await database.commit()

    def _connect(self):
        return _Connection(self.database_path)

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()


class _Connection:
    def __init__(self, path: Path):
        self.path = path
        self.connection: aiosqlite.Connection | None = None

    async def __aenter__(self) -> aiosqlite.Connection:
        self.connection = await aiosqlite.connect(self.path, timeout=5)
        self.connection.row_factory = aiosqlite.Row
        await self.connection.execute("PRAGMA busy_timeout=5000")
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        if self.connection is not None:
            if exc_type is not None:
                await self.connection.rollback()
            await self.connection.close()
