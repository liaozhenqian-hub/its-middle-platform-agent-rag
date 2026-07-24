from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import AsyncIterator

import aiosqlite


class ConversationScopeConflictError(RuntimeError):
    pass


@dataclass(frozen=True)
class ConversationScope:
    conversation_id: str
    knowledge_space_id: str
    domain_id: str | None
    created_at: datetime


class ConversationScopeRepository:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[aiosqlite.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        db = await aiosqlite.connect(self.db_path)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA busy_timeout=5000")
        try:
            yield db
        finally:
            await db.close()

    async def initialize(self) -> None:
        async with self._connect() as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_conversation_scopes (
                    conversation_id TEXT PRIMARY KEY,
                    knowledge_space_id TEXT NOT NULL,
                    domain_id TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            await db.commit()

    async def bind(
        self,
        conversation_id: str,
        knowledge_space_id: str,
        domain_id: str | None,
    ) -> ConversationScope:
        conversation_id = conversation_id.strip()
        knowledge_space_id = knowledge_space_id.strip()
        domain_id = domain_id.strip() if domain_id and domain_id.strip() else None
        if not conversation_id or not knowledge_space_id:
            raise ValueError("conversation and knowledge space IDs are required")

        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                row = await (
                    await db.execute(
                        "SELECT * FROM agent_conversation_scopes WHERE conversation_id=?",
                        (conversation_id,),
                    )
                ).fetchone()
                if row is None:
                    created_at = datetime.now(UTC).isoformat()
                    await db.execute(
                        """
                        INSERT INTO agent_conversation_scopes(
                            conversation_id, knowledge_space_id, domain_id, created_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (conversation_id, knowledge_space_id, domain_id, created_at),
                    )
                    row = await (
                        await db.execute(
                            "SELECT * FROM agent_conversation_scopes WHERE conversation_id=?",
                            (conversation_id,),
                        )
                    ).fetchone()
                elif (
                    row["knowledge_space_id"] != knowledge_space_id
                    or row["domain_id"] != domain_id
                ):
                    raise ConversationScopeConflictError(
                        "conversation is already bound to a different knowledge scope"
                    )
                await db.commit()
            except Exception:
                if db.in_transaction:
                    await db.rollback()
                raise
        return self._from_row(row)

    async def get(self, conversation_id: str) -> ConversationScope | None:
        async with self._connect() as db:
            row = await (
                await db.execute(
                    "SELECT * FROM agent_conversation_scopes WHERE conversation_id=?",
                    (conversation_id,),
                )
            ).fetchone()
        return self._from_row(row) if row is not None else None

    async def delete(self, conversation_id: str) -> bool:
        async with self._connect() as db:
            cursor = await db.execute(
                "DELETE FROM agent_conversation_scopes WHERE conversation_id=?",
                (conversation_id,),
            )
            await db.commit()
            return cursor.rowcount == 1

    @staticmethod
    def _from_row(row: aiosqlite.Row) -> ConversationScope:
        return ConversationScope(
            conversation_id=row["conversation_id"],
            knowledge_space_id=row["knowledge_space_id"],
            domain_id=row["domain_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )
