from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
from sqlalchemy import select, update, func
from sqlalchemy.dialects.postgresql import insert as postgres_insert

from knowledge.persistence.database import DatabaseResources
from knowledge.persistence.schema import feishu_events


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


class PostgresFeishuEventRepository:
    def __init__(self, database_resources: DatabaseResources):
        self.database_resources = database_resources

    async def initialize(self) -> None:
        async with self.database_resources.transaction() as connection:
            await connection.execute(
                update(feishu_events)
                .where(feishu_events.c.status == "processing")
                .values(
                    status="failed",
                    error_type="ServiceRestart",
                    updated_at=func.now(),
                )
            )

    async def claim(self, event_id: str, message_id: str, chat_id: str) -> bool:
        event_id = event_id.strip()
        message_id = message_id.strip()
        chat_id = chat_id.strip()
        if not event_id or not message_id or not chat_id:
            raise ValueError("event_id, message_id and chat_id are required")
        async with self.database_resources.transaction() as connection:
            inserted = (
                await connection.execute(
                    postgres_insert(feishu_events)
                    .values(
                        event_id=event_id,
                        message_id=message_id,
                        chat_id=chat_id,
                        status="processing",
                        attempt=1,
                    )
                    .on_conflict_do_nothing(index_elements=[feishu_events.c.event_id])
                    .returning(feishu_events.c.event_id)
                )
            ).scalar_one_or_none()
            if inserted is not None:
                return True
            retried = (
                await connection.execute(
                    update(feishu_events)
                    .where(
                        feishu_events.c.event_id == event_id,
                        feishu_events.c.status == "failed",
                        feishu_events.c.attempt < 2,
                    )
                    .values(
                        message_id=message_id,
                        chat_id=chat_id,
                        status="processing",
                        attempt=feishu_events.c.attempt + 1,
                        error_type=None,
                        updated_at=func.now(),
                    )
                    .returning(feishu_events.c.event_id)
                )
            ).scalar_one_or_none()
        return retried is not None

    async def complete(self, event_id: str) -> None:
        await self._set_status(event_id, "completed", None)

    async def fail(self, event_id: str, error_type: str) -> None:
        sanitized = _ERROR_TYPE_PATTERN.sub("", str(error_type))[:100] or "Error"
        await self._set_status(event_id, "failed", sanitized)

    async def _set_status(
        self, event_id: str, status: str, error_type: str | None
    ) -> None:
        async with self.database_resources.transaction() as connection:
            await connection.execute(
                update(feishu_events)
                .where(
                    feishu_events.c.event_id == event_id,
                    feishu_events.c.status == "processing",
                )
                .values(status=status, error_type=error_type, updated_at=func.now())
            )
