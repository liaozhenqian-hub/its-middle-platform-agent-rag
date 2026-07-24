from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite


class PendingRunNotFoundError(LookupError):
    pass


class PendingRunConflictError(RuntimeError):
    pass


@dataclass(frozen=True)
class PendingRun:
    run_id: str
    conversation_id: str
    state: dict[str, Any]
    approvals: list[dict[str, Any]]
    status: str


class PendingRunRepository:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    async def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_pending_runs (
                    run_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    approvals_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_agent_pending_runs_conversation
                ON agent_pending_runs(conversation_id)
                """
            )
            await db.commit()

    async def save_pending(
        self,
        run_id: str,
        conversation_id: str,
        state: dict[str, Any],
        approvals: list[dict[str, Any]],
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO agent_pending_runs (
                    run_id, conversation_id, state_json, approvals_json, status
                ) VALUES (?, ?, ?, ?, 'pending')
                ON CONFLICT(run_id) DO UPDATE SET
                    state_json=excluded.state_json,
                    approvals_json=excluded.approvals_json,
                    status='pending',
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    run_id,
                    conversation_id,
                    json.dumps(state, ensure_ascii=False),
                    json.dumps(approvals, ensure_ascii=False),
                ),
            )
            await db.commit()

    async def get_pending(self, run_id: str) -> PendingRun:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT run_id, conversation_id, state_json, approvals_json, status
                FROM agent_pending_runs WHERE run_id = ?
                """,
                (run_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            raise PendingRunNotFoundError(run_id)
        if row[4] != "pending":
            raise PendingRunConflictError(run_id)
        return PendingRun(
            run_id=row[0],
            conversation_id=row[1],
            state=json.loads(row[2]),
            approvals=json.loads(row[3]),
            status=row[4],
        )

    async def mark_completed(self, run_id: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                UPDATE agent_pending_runs
                SET status='completed', updated_at=CURRENT_TIMESTAMP
                WHERE run_id=? AND status='pending'
                """,
                (run_id,),
            )
            if cursor.rowcount == 1:
                await db.commit()
                return
            exists_cursor = await db.execute(
                "SELECT 1 FROM agent_pending_runs WHERE run_id=?",
                (run_id,),
            )
            exists = await exists_cursor.fetchone()
            await db.rollback()
        if exists is None:
            raise PendingRunNotFoundError(run_id)
        raise PendingRunConflictError(run_id)

    async def delete_conversation(self, conversation_id: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM agent_pending_runs WHERE conversation_id=?",
                (conversation_id,),
            )
            await db.commit()

    async def check_ready(self) -> bool:
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("SELECT 1")
            return True
        except Exception:
            return False
