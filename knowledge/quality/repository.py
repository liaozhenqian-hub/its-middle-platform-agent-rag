from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Coroutine, TypeVar
from uuid import uuid4

import aiosqlite

from knowledge.quality.models import (
    CitationSnapshot,
    EvalCase,
    EvalCaseCreate,
    EvalResult,
    EvalRun,
    FeedbackRecord,
    QualityAnalytics,
    QualityAnnotation,
    QualityAnnotationCreate,
    QualityAnnotationPage,
    QualitySpan,
    QualitySpanCreate,
    QualityTurn,
    QualityTurnPage,
    ToolRunSnapshot,
    TurnCompletion,
    TurnStart,
)


_T = TypeVar("_T")
_TERMINAL_STATUSES = {
    "completed",
    "approval_required",
    "clarification_required",
    "no_answer",
    "error",
    "timeout",
    "cancelled",
    "interrupted",
}
_BLOCKED_AUDIT_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "body",
    "chunk",
    "content",
    "embedding",
    "output",
    "password",
    "prompt",
    "secret",
    "token",
}


class QualityNotFoundError(LookupError):
    pass


class InvalidFeedbackTokenError(PermissionError):
    pass


class QualityRepository:
    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)

    async def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        async with self._connect() as database:
            await database.execute("PRAGMA journal_mode=WAL")
            await database.executescript(
                """
                CREATE TABLE IF NOT EXISTS quality_schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS quality_turns (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL UNIQUE,
                    conversation_id TEXT NOT NULL DEFAULT '',
                    channel TEXT NOT NULL CHECK(channel IN ('web','api','feishu','eval','codex')),
                    channel_message_id TEXT,
                    user_id TEXT,
                    user_name TEXT,
                    chat_id TEXT,
                    question TEXT NOT NULL,
                    answer TEXT,
                    knowledge_space_id TEXT NOT NULL DEFAULT 'middle-platform',
                    domain_id TEXT,
                    status TEXT NOT NULL,
                    provider TEXT NOT NULL DEFAULT '',
                    model_name TEXT NOT NULL DEFAULT '',
                    last_agent TEXT NOT NULL DEFAULT '',
                    application_version TEXT NOT NULL DEFAULT '',
                    prompt_version TEXT NOT NULL DEFAULT '',
                    duration_ms REAL,
                    error_type TEXT,
                    feedback_token_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_quality_turn_channel_message
                    ON quality_turns(channel, channel_message_id)
                    WHERE channel_message_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_quality_turn_created ON quality_turns(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_quality_turn_status ON quality_turns(status);
                CREATE INDEX IF NOT EXISTS idx_quality_turn_channel ON quality_turns(channel);
                CREATE INDEX IF NOT EXISTS idx_quality_turn_user ON quality_turns(user_id);
                CREATE INDEX IF NOT EXISTS idx_quality_turn_domain ON quality_turns(domain_id);

                CREATE TABLE IF NOT EXISTS quality_tool_runs (
                    id TEXT PRIMARY KEY,
                    turn_id TEXT NOT NULL REFERENCES quality_turns(id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    tool_call_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    duration_ms REAL,
                    arguments_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS quality_citations (
                    id TEXT PRIMARY KEY,
                    turn_id TEXT NOT NULL REFERENCES quality_turns(id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS quality_feedback (
                    id TEXT PRIMARY KEY,
                    turn_id TEXT NOT NULL REFERENCES quality_turns(id) ON DELETE CASCADE,
                    channel TEXT NOT NULL,
                    feedback_key TEXT NOT NULL,
                    user_id TEXT,
                    user_name TEXT,
                    rating TEXT NOT NULL CHECK(rating IN ('positive','negative')),
                    reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(turn_id, channel, feedback_key)
                );
                CREATE INDEX IF NOT EXISTS idx_quality_feedback_rating ON quality_feedback(rating);

                CREATE TABLE IF NOT EXISTS eval_cases (
                    id TEXT PRIMARY KEY,
                    source_turn_id TEXT REFERENCES quality_turns(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    question TEXT NOT NULL,
                    knowledge_space_id TEXT NOT NULL,
                    domain_id TEXT,
                    required_tools_json TEXT NOT NULL,
                    required_citation_types_json TEXT NOT NULL,
                    required_facts_json TEXT NOT NULL,
                    forbidden_facts_json TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS eval_runs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    application_version TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    total_cases INTEGER NOT NULL,
                    passed_cases INTEGER NOT NULL DEFAULT 0,
                    failed_cases INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS eval_results (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES eval_runs(id) ON DELETE CASCADE,
                    case_id TEXT NOT NULL REFERENCES eval_cases(id) ON DELETE CASCADE,
                    status TEXT NOT NULL,
                    answer TEXT,
                    last_agent TEXT NOT NULL,
                    tool_names_json TEXT NOT NULL,
                    citation_types_json TEXT NOT NULL,
                    duration_ms REAL,
                    checks_json TEXT NOT NULL,
                    passed INTEGER NOT NULL CHECK(passed IN (0,1)),
                    error_type TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, case_id)
                );

                INSERT OR IGNORE INTO quality_schema_migrations(version, applied_at)
                VALUES(1, CURRENT_TIMESTAMP);
                INSERT OR IGNORE INTO quality_schema_migrations(version, applied_at)
                VALUES(2, CURRENT_TIMESTAMP);
                """
            )
            columns = {
                row["name"]
                for row in await (
                    await database.execute("PRAGMA table_info(quality_turns)")
                ).fetchall()
            }
            if "channel_reply_message_id" not in columns:
                await database.execute(
                    "ALTER TABLE quality_turns ADD COLUMN channel_reply_message_id TEXT"
                )
            await database.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_quality_turn_channel_reply "
                "ON quality_turns(channel, channel_reply_message_id) "
                "WHERE channel_reply_message_id IS NOT NULL"
            )
            await database.execute(
                "INSERT OR IGNORE INTO quality_schema_migrations(version, applied_at) "
                "VALUES(3, ?)",
                (self._now(),),
            )
            eval_columns = {
                row["name"]
                for row in await (
                    await database.execute("PRAGMA table_info(eval_cases)")
                ).fetchall()
            }
            eval_migrations = {
                "expected_behavior": (
                    "ALTER TABLE eval_cases ADD COLUMN expected_behavior "
                    "TEXT NOT NULL DEFAULT 'answer'"
                ),
                "max_latency_ms": (
                    "ALTER TABLE eval_cases ADD COLUMN max_latency_ms "
                    "REAL NOT NULL DEFAULT 60000"
                ),
                "max_tool_calls": (
                    "ALTER TABLE eval_cases ADD COLUMN max_tool_calls "
                    "INTEGER NOT NULL DEFAULT 6"
                ),
                "max_citations": (
                    "ALTER TABLE eval_cases ADD COLUMN max_citations "
                    "INTEGER NOT NULL DEFAULT 10"
                ),
            }
            for column, statement in eval_migrations.items():
                if column not in eval_columns:
                    await database.execute(statement)
            await database.execute(
                "INSERT OR IGNORE INTO quality_schema_migrations(version, applied_at) "
                "VALUES(4, ?)",
                (self._now(),),
            )
            specialist_domains = (
                ("approval_flow_expert", "approval-flow"),
                ("workflow_expert", "workflow"),
                ("metric_platform_expert", "metric-platform"),
                ("bug_diagnosis_expert", "bug"),
            )
            specialist_tools = tuple(item[0] for item in specialist_domains)
            for tool_name, domain_id in specialist_domains:
                placeholders = ",".join("?" for _ in specialist_tools)
                await database.execute(
                    f"""
                    UPDATE quality_turns
                    SET domain_id=?
                    WHERE domain_id IS NULL
                      AND EXISTS(
                          SELECT 1 FROM quality_tool_runs own
                          WHERE own.turn_id=quality_turns.id AND own.tool_name=?
                      )
                      AND NOT EXISTS(
                          SELECT 1 FROM quality_tool_runs other
                          WHERE other.turn_id=quality_turns.id
                            AND other.tool_name IN ({placeholders})
                            AND other.tool_name<>?
                      )
                    """,
                    (domain_id, tool_name, *specialist_tools, tool_name),
                )
            await database.execute(
                "INSERT OR IGNORE INTO quality_schema_migrations(version, applied_at) "
                "VALUES(5, ?)",
                (self._now(),),
            )
            await self._migrate_quality_v2(database)
            await database.commit()

    async def _migrate_quality_v2(self, database: aiosqlite.Connection) -> None:
        schema_row = await (
            await database.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='quality_turns'"
            )
        ).fetchone()
        schema_sql = str(schema_row["sql"] or "") if schema_row else ""
        if "'codex'" not in schema_sql:
            await database.commit()
            await database.execute("PRAGMA foreign_keys=OFF")
            await database.execute("PRAGMA legacy_alter_table=ON")
            await database.execute("ALTER TABLE quality_turns RENAME TO quality_turns_v1")
            await database.execute(
                """
                CREATE TABLE quality_turns (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL UNIQUE,
                    conversation_id TEXT NOT NULL DEFAULT '',
                    channel TEXT NOT NULL CHECK(channel IN ('web','api','feishu','eval','codex')),
                    channel_message_id TEXT,
                    user_id TEXT,
                    user_name TEXT,
                    chat_id TEXT,
                    question TEXT NOT NULL,
                    answer TEXT,
                    knowledge_space_id TEXT NOT NULL DEFAULT 'middle-platform',
                    domain_id TEXT,
                    status TEXT NOT NULL,
                    provider TEXT NOT NULL DEFAULT '',
                    model_name TEXT NOT NULL DEFAULT '',
                    last_agent TEXT NOT NULL DEFAULT '',
                    application_version TEXT NOT NULL DEFAULT '',
                    prompt_version TEXT NOT NULL DEFAULT '',
                    duration_ms REAL,
                    error_type TEXT,
                    feedback_token_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    channel_reply_message_id TEXT
                )
                """
            )
            columns = [
                row["name"]
                for row in await (
                    await database.execute("PRAGMA table_info(quality_turns_v1)")
                ).fetchall()
            ]
            copied = [
                name
                for name in columns
                if name
                in {
                    "id", "run_id", "conversation_id", "channel",
                    "channel_message_id", "user_id", "user_name", "chat_id",
                    "question", "answer", "knowledge_space_id", "domain_id",
                    "status", "provider", "model_name", "last_agent",
                    "application_version", "prompt_version", "duration_ms",
                    "error_type", "feedback_token_hash", "created_at", "updated_at",
                    "completed_at", "channel_reply_message_id",
                }
            ]
            names = ",".join(copied)
            await database.execute(
                f"INSERT INTO quality_turns({names}) SELECT {names} FROM quality_turns_v1"
            )
            await database.execute("DROP TABLE quality_turns_v1")
            await database.execute("PRAGMA legacy_alter_table=OFF")
            await database.execute("PRAGMA foreign_keys=ON")
            await database.executescript(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_quality_turn_channel_message
                    ON quality_turns(channel, channel_message_id)
                    WHERE channel_message_id IS NOT NULL;
                CREATE UNIQUE INDEX IF NOT EXISTS idx_quality_turn_channel_reply
                    ON quality_turns(channel, channel_reply_message_id)
                    WHERE channel_reply_message_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_quality_turn_created ON quality_turns(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_quality_turn_status ON quality_turns(status);
                CREATE INDEX IF NOT EXISTS idx_quality_turn_channel ON quality_turns(channel);
                CREATE INDEX IF NOT EXISTS idx_quality_turn_user ON quality_turns(user_id);
                CREATE INDEX IF NOT EXISTS idx_quality_turn_domain ON quality_turns(domain_id);
                """
            )

        turn_columns = {
            row["name"]
            for row in await (
                await database.execute("PRAGMA table_info(quality_turns)")
            ).fetchall()
        }
        for column, statement in {
            "routed_domains_json": "ALTER TABLE quality_turns ADD COLUMN routed_domains_json TEXT NOT NULL DEFAULT '[]'",
            "specialists_used_json": "ALTER TABLE quality_turns ADD COLUMN specialists_used_json TEXT NOT NULL DEFAULT '[]'",
        }.items():
            if column not in turn_columns:
                await database.execute(statement)

        feedback_columns = {
            row["name"]
            for row in await (
                await database.execute("PRAGMA table_info(quality_feedback)")
            ).fetchall()
        }
        if "reason_code" not in feedback_columns:
            await database.execute(
                "ALTER TABLE quality_feedback ADD COLUMN reason_code TEXT NOT NULL DEFAULT ''"
            )
        await database.executescript(
            """
            CREATE TABLE IF NOT EXISTS quality_spans (
                id TEXT PRIMARY KEY,
                turn_id TEXT NOT NULL REFERENCES quality_turns(id) ON DELETE CASCADE,
                run_id TEXT NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('agent','llm','tool','graph')),
                name TEXT NOT NULL,
                status TEXT NOT NULL,
                duration_ms REAL,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL DEFAULT 0,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_quality_spans_turn ON quality_spans(turn_id, created_at);
            CREATE TABLE IF NOT EXISTS quality_annotations (
                id TEXT PRIMARY KEY,
                turn_id TEXT NOT NULL REFERENCES quality_turns(id) ON DELETE CASCADE,
                source TEXT NOT NULL CHECK(source IN ('rule','judge','manual')),
                code TEXT NOT NULL,
                severity TEXT NOT NULL CHECK(severity IN ('info','warning','error','critical')),
                confidence REAL NOT NULL,
                details_json TEXT NOT NULL DEFAULT '{}',
                review_status TEXT NOT NULL DEFAULT 'pending' CHECK(review_status IN ('pending','confirmed','dismissed')),
                reviewer TEXT,
                reviewed_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_quality_annotations_code ON quality_annotations(code, review_status);
            CREATE INDEX IF NOT EXISTS idx_quality_annotations_turn ON quality_annotations(turn_id, created_at);
            INSERT OR IGNORE INTO quality_schema_migrations(version, applied_at)
            VALUES(6, CURRENT_TIMESTAMP);
            """
        )
        eval_case_columns = {
            row["name"] for row in await (
                await database.execute("PRAGMA table_info(eval_cases)")
            ).fetchall()
        }
        for column, statement in {
            "turns_json": "ALTER TABLE eval_cases ADD COLUMN turns_json TEXT NOT NULL DEFAULT '[]'",
            "task_type": "ALTER TABLE eval_cases ADD COLUMN task_type TEXT NOT NULL DEFAULT 'unknown'",
            "suite": "ALTER TABLE eval_cases ADD COLUMN suite TEXT NOT NULL DEFAULT 'routing-breadth'",
            "priority": "ALTER TABLE eval_cases ADD COLUMN priority TEXT NOT NULL DEFAULT 'normal'",
            "approval_state": "ALTER TABLE eval_cases ADD COLUMN approval_state TEXT NOT NULL DEFAULT 'candidate'",
            "version": "ALTER TABLE eval_cases ADD COLUMN version INTEGER NOT NULL DEFAULT 1",
        }.items():
            if column not in eval_case_columns:
                await database.execute(statement)
        eval_run_columns = {
            row["name"] for row in await (
                await database.execute("PRAGMA table_info(eval_runs)")
            ).fetchall()
        }
        for column, statement in {
            "case_ids_json": "ALTER TABLE eval_runs ADD COLUMN case_ids_json TEXT NOT NULL DEFAULT '[]'",
            "config_snapshot_json": "ALTER TABLE eval_runs ADD COLUMN config_snapshot_json TEXT NOT NULL DEFAULT '{}'",
            "cancel_requested": "ALTER TABLE eval_runs ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0",
            "current_case": "ALTER TABLE eval_runs ADD COLUMN current_case INTEGER NOT NULL DEFAULT 0",
            "updated_at": "ALTER TABLE eval_runs ADD COLUMN updated_at TEXT",
        }.items():
            if column not in eval_run_columns:
                await database.execute(statement)
        await database.execute("UPDATE eval_runs SET updated_at=COALESCE(updated_at, created_at)")
        eval_result_columns = {
            row["name"] for row in await (
                await database.execute("PRAGMA table_info(eval_results)")
            ).fetchall()
        }
        for column, statement in {
            "judge_score": "ALTER TABLE eval_results ADD COLUMN judge_score REAL",
            "judge_json": "ALTER TABLE eval_results ADD COLUMN judge_json TEXT NOT NULL DEFAULT '{}'",
            "failure_codes_json": "ALTER TABLE eval_results ADD COLUMN failure_codes_json TEXT NOT NULL DEFAULT '[]'",
            "review_state": "ALTER TABLE eval_results ADD COLUMN review_state TEXT NOT NULL DEFAULT 'not_required'",
            "case_snapshot_json": "ALTER TABLE eval_results ADD COLUMN case_snapshot_json TEXT NOT NULL DEFAULT '{}'",
        }.items():
            if column not in eval_result_columns:
                await database.execute(statement)
        await database.execute(
            "INSERT OR IGNORE INTO quality_schema_migrations(version, applied_at) VALUES(7, ?)",
            (self._now(),),
        )

    async def check_ready(self) -> bool:
        try:
            async with self._connect() as database:
                row = await (await database.execute("SELECT 1")).fetchone()
            return row is not None
        except (OSError, sqlite3.Error):
            return False

    async def start_turn(self, value: TurnStart) -> QualityTurn:
        if not value.run_id.strip() or not value.question.strip():
            raise ValueError("run_id and question are required")
        if value.channel not in {"web", "api", "feishu", "eval", "codex"}:
            raise ValueError("unsupported quality channel")
        token = secrets.token_urlsafe(32)
        now = self._now()
        turn_id = str(uuid4())

        async def operation(database: aiosqlite.Connection) -> None:
            await database.execute(
                """
                INSERT OR IGNORE INTO quality_turns(
                    id, run_id, conversation_id, channel, channel_message_id,
                    user_id, user_name, chat_id, question, knowledge_space_id,
                    domain_id, status, provider, model_name, application_version,
                    prompt_version, feedback_token_hash, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,'running',?,?,?,?,?,?,?)
                """,
                (
                    turn_id,
                    value.run_id.strip(),
                    value.conversation_id.strip(),
                    value.channel,
                    self._optional(value.channel_message_id),
                    self._optional(value.user_id),
                    self._optional(value.user_name),
                    self._optional(value.chat_id),
                    value.question,
                    value.knowledge_space_id or "middle-platform",
                    self._optional(value.domain_id),
                    value.provider,
                    value.model_name,
                    value.application_version,
                    value.prompt_version,
                    self._token_hash(token),
                    now,
                    now,
                ),
            )

        await self._write(operation)
        stored = await self.get_turn_by_run_id(value.run_id)
        if stored is None:
            raise RuntimeError("quality turn could not be created")
        return replace(stored, feedback_token=token if stored.id == turn_id else "")

    async def complete_turn(self, run_id: str, value: TurnCompletion) -> QualityTurn:
        if value.status not in _TERMINAL_STATUSES:
            raise ValueError("unsupported terminal quality status")
        now = self._now()

        async def operation(database: aiosqlite.Connection) -> None:
            row = await (
                await database.execute(
                    "SELECT id FROM quality_turns WHERE run_id=?", (run_id,)
                )
            ).fetchone()
            if row is None:
                raise QualityNotFoundError(run_id)
            turn_id = str(row["id"])
            await database.execute(
                """
                UPDATE quality_turns
                SET status=?, answer=?, last_agent=?,
                    domain_id=COALESCE(?, domain_id), duration_ms=?, error_type=?,
                    routed_domains_json=?, specialists_used_json=?,
                    updated_at=?, completed_at=?
                WHERE id=?
                """,
                (
                    value.status,
                    value.answer,
                    value.last_agent,
                    self._optional(value.domain_id),
                    value.duration_ms,
                    self._optional(value.error_type),
                    self._json(value.routed_domains),
                    self._json(value.specialists_used),
                    now,
                    now,
                    turn_id,
                ),
            )
            await database.execute("DELETE FROM quality_tool_runs WHERE turn_id=?", (turn_id,))
            await database.execute("DELETE FROM quality_citations WHERE turn_id=?", (turn_id,))
            for sequence, item in enumerate(value.tools):
                await database.execute(
                    """
                    INSERT INTO quality_tool_runs(
                        id, turn_id, sequence, tool_call_id, tool_name, agent_name,
                        status, duration_ms, arguments_json
                    ) VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        str(uuid4()),
                        turn_id,
                        sequence,
                        item.tool_call_id,
                        item.tool_name,
                        item.agent_name,
                        item.status,
                        item.duration_ms,
                        self._json(self._sanitize_audit(item.arguments)),
                    ),
                )
            for sequence, item in enumerate(value.citations):
                await database.execute(
                    """
                    INSERT INTO quality_citations(
                        id, turn_id, sequence, source_type, source_id, title,
                        domain, metadata_json
                    ) VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (
                        str(uuid4()),
                        turn_id,
                        sequence,
                        item.source_type,
                        item.source_id,
                        item.title,
                        item.domain,
                        self._json(self._sanitize_audit(item.metadata)),
                    ),
                )

        await self._write(operation)
        stored = await self.get_turn_by_run_id(run_id)
        if stored is None:
            raise QualityNotFoundError(run_id)
        return stored

    async def bind_channel_reply(self, run_id: str, message_id: str) -> None:
        async def operation(database: aiosqlite.Connection) -> None:
            cursor = await database.execute(
                "UPDATE quality_turns SET channel_reply_message_id=?, updated_at=? WHERE run_id=?",
                (message_id, self._now(), run_id),
            )
            if cursor.rowcount == 0:
                raise QualityNotFoundError(run_id)

        await self._write(operation)

    async def get_turn(self, turn_id: str) -> QualityTurn | None:
        async with self._connect() as database:
            row = await (
                await database.execute("SELECT * FROM quality_turns WHERE id=?", (turn_id,))
            ).fetchone()
            return await self._turn_from_row(database, row, details=True) if row else None

    async def get_turn_by_run_id(self, run_id: str) -> QualityTurn | None:
        async with self._connect() as database:
            row = await (
                await database.execute("SELECT * FROM quality_turns WHERE run_id=?", (run_id,))
            ).fetchone()
            return await self._turn_from_row(database, row, details=True) if row else None

    async def get_previous_conversation_turn(
        self, conversation_id: str, current_turn_id: str
    ) -> QualityTurn | None:
        if not conversation_id:
            return None
        async with self._connect() as database:
            row = await (await database.execute(
                """
                SELECT * FROM quality_turns
                WHERE conversation_id=? AND id<>?
                ORDER BY created_at DESC LIMIT 1
                """,
                (conversation_id, current_turn_id),
            )).fetchone()
            return await self._turn_from_row(database, row, details=False) if row else None

    async def get_turn_by_channel_message(
        self, channel: str, message_id: str
    ) -> QualityTurn | None:
        async with self._connect() as database:
            row = await (
                await database.execute(
                    "SELECT * FROM quality_turns WHERE channel=? AND channel_message_id=?",
                    (channel, message_id),
                )
            ).fetchone()
            return await self._turn_from_row(database, row, details=True) if row else None

    async def get_turn_by_channel_reply(
        self, channel: str, message_id: str
    ) -> QualityTurn | None:
        async with self._connect() as database:
            row = await (
                await database.execute(
                    "SELECT * FROM quality_turns WHERE channel=? AND channel_reply_message_id=?",
                    (channel, message_id),
                )
            ).fetchone()
            return await self._turn_from_row(database, row, details=True) if row else None

    async def get_latest_bug_context(
        self,
        conversation_id: str,
    ) -> dict[str, str | None] | None:
        async with self._connect() as database:
            row = await (
                await database.execute(
                    """
                    SELECT c.source_id, c.metadata_json
                    FROM quality_turns t
                    JOIN quality_citations c ON c.turn_id=t.id
                    WHERE t.conversation_id=?
                      AND c.source_type='log_trace'
                      AND datetime(t.created_at) >= datetime('now', '-1 day')
                    ORDER BY t.created_at DESC, c.sequence DESC
                    LIMIT 1
                    """,
                    (conversation_id,),
                )
            ).fetchone()
        if row is None:
            return None
        metadata = json.loads(row["metadata_json"] or "{}")
        environment = str(metadata.get("environment") or "").strip()
        trace_id = str(row["source_id"] or "").strip()
        if environment not in {"develop", "test", "prod"} or not trace_id:
            return None
        return {
            "environment": environment,
            "trace_id": trace_id,
            "request_time": None,
        }

    async def list_turns(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        channel: str | None = None,
        status: str | None = None,
        rating: str | None = None,
        domain_id: str | None = None,
        user_id: str | None = None,
        query: str | None = None,
    ) -> QualityTurnPage:
        page = max(1, int(page))
        page_size = min(200, max(1, int(page_size)))
        clauses: list[str] = []
        parameters: list[Any] = []
        for column, value in (
            ("t.channel", channel),
            ("t.status", status),
            ("t.domain_id", domain_id),
            ("t.user_id", user_id),
        ):
            if value:
                clauses.append(f"{column}=?")
                parameters.append(value)
        if rating:
            clauses.append(
                "EXISTS(SELECT 1 FROM quality_feedback f WHERE f.turn_id=t.id AND f.rating=?)"
            )
            parameters.append(rating)
        if query:
            clauses.append("(t.question LIKE ? OR COALESCE(t.answer,'') LIKE ?)")
            pattern = f"%{query}%"
            parameters.extend((pattern, pattern))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        async with self._connect() as database:
            total = int(
                (
                    await (
                        await database.execute(
                            f"SELECT COUNT(*) FROM quality_turns t {where}", parameters
                        )
                    ).fetchone()
                )[0]
            )
            rows = await (
                await database.execute(
                    f"SELECT t.* FROM quality_turns t {where} "
                    "ORDER BY t.created_at DESC LIMIT ? OFFSET ?",
                    [*parameters, page_size, (page - 1) * page_size],
                )
            ).fetchall()
            items = [await self._turn_from_row(database, row, details=False) for row in rows]
        return QualityTurnPage(items=items, page=page, page_size=page_size, total=total)

    async def upsert_feedback(
        self,
        *,
        turn_id: str,
        feedback_token: str | None,
        rating: str,
        reason: str = "",
        reason_code: str = "",
        user_id: str | None = None,
        user_name: str | None = None,
        channel: str = "web",
        trusted: bool = False,
    ) -> FeedbackRecord:
        if rating not in {"positive", "negative"}:
            raise ValueError("rating must be positive or negative")
        now = self._now()
        feedback_id = str(uuid4())
        feedback_key = user_id or "anonymous"

        async def operation(database: aiosqlite.Connection) -> None:
            turn = await (
                await database.execute(
                    "SELECT feedback_token_hash FROM quality_turns WHERE id=?", (turn_id,)
                )
            ).fetchone()
            if turn is None:
                raise QualityNotFoundError(turn_id)
            if not trusted and not self._valid_token(
                feedback_token or "", str(turn["feedback_token_hash"])
            ):
                raise InvalidFeedbackTokenError(turn_id)
            await database.execute(
                """
                INSERT INTO quality_feedback(
                    id, turn_id, channel, feedback_key, user_id, user_name,
                    rating, reason, reason_code, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(turn_id, channel, feedback_key) DO UPDATE SET
                    user_name=excluded.user_name,
                    rating=excluded.rating,
                    reason=excluded.reason,
                    reason_code=excluded.reason_code,
                    updated_at=excluded.updated_at
                """,
                (
                    feedback_id,
                    turn_id,
                    channel,
                    feedback_key,
                    self._optional(user_id),
                    self._optional(user_name),
                    rating,
                    reason[:1000],
                    reason_code[:100],
                    now,
                    now,
                ),
            )

        await self._write(operation)
        async with self._connect() as database:
            row = await (
                await database.execute(
                    "SELECT * FROM quality_feedback WHERE turn_id=? AND channel=? AND feedback_key=?",
                    (turn_id, channel, feedback_key),
                )
            ).fetchone()
        return self._feedback_from_row(row)

    async def record_span(self, value: QualitySpanCreate) -> QualitySpan:
        if value.kind not in {"agent", "llm", "tool", "graph"}:
            raise ValueError("unsupported quality span kind")
        span_id = str(uuid4())
        now = self._now()
        metadata = self._sanitize_audit(value.metadata)

        async def operation(database: aiosqlite.Connection) -> None:
            await database.execute(
                """
                INSERT INTO quality_spans(
                    id, turn_id, run_id, kind, name, status, duration_ms,
                    input_tokens, output_tokens, total_tokens, metadata_json, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    span_id, value.turn_id, value.run_id, value.kind, value.name,
                    value.status, value.duration_ms, max(0, value.input_tokens),
                    max(0, value.output_tokens), max(0, value.total_tokens),
                    self._json(metadata), now,
                ),
            )

        await self._write(operation)
        async with self._connect() as database:
            row = await (
                await database.execute("SELECT * FROM quality_spans WHERE id=?", (span_id,))
            ).fetchone()
        return self._span_from_row(row)

    async def create_annotation(
        self, value: QualityAnnotationCreate
    ) -> QualityAnnotation:
        if value.source not in {"rule", "judge", "manual"}:
            raise ValueError("unsupported annotation source")
        if value.severity not in {"info", "warning", "error", "critical"}:
            raise ValueError("unsupported annotation severity")
        if not 0 <= value.confidence <= 1:
            raise ValueError("annotation confidence must be between 0 and 1")
        annotation_id = str(uuid4())
        now = self._now()

        async def operation(database: aiosqlite.Connection) -> None:
            await database.execute(
                """
                INSERT INTO quality_annotations(
                    id, turn_id, source, code, severity, confidence, details_json,
                    review_status, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,'pending',?,?)
                """,
                (
                    annotation_id, value.turn_id, value.source, value.code,
                    value.severity, value.confidence,
                    self._json(self._sanitize_audit(value.details)), now, now,
                ),
            )

        await self._write(operation)
        return await self._get_annotation(annotation_id)

    async def list_annotations(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        code: str | None = None,
        review_status: str | None = None,
        source: str | None = None,
    ) -> QualityAnnotationPage:
        page = max(1, int(page))
        page_size = min(200, max(1, int(page_size)))
        clauses: list[str] = []
        parameters: list[Any] = []
        for column, value in (("code", code), ("review_status", review_status), ("source", source)):
            if value:
                clauses.append(f"{column}=?")
                parameters.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        async with self._connect() as database:
            total = int((await (await database.execute(
                f"SELECT COUNT(*) FROM quality_annotations {where}", parameters
            )).fetchone())[0])
            rows = await (await database.execute(
                f"SELECT * FROM quality_annotations {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                [*parameters, page_size, (page - 1) * page_size],
            )).fetchall()
        return QualityAnnotationPage(
            items=[self._annotation_from_row(row) for row in rows],
            page=page,
            page_size=page_size,
            total=total,
        )

    async def update_annotation_review(
        self, annotation_id: str, *, review_status: str, reviewer: str
    ) -> QualityAnnotation:
        if review_status not in {"pending", "confirmed", "dismissed"}:
            raise ValueError("unsupported annotation review status")
        now = self._now()

        async def operation(database: aiosqlite.Connection) -> None:
            cursor = await database.execute(
                """
                UPDATE quality_annotations
                SET review_status=?, reviewer=?, reviewed_at=?, updated_at=?
                WHERE id=?
                """,
                (review_status, reviewer, now if review_status != "pending" else None, now, annotation_id),
            )
            if cursor.rowcount == 0:
                raise QualityNotFoundError(annotation_id)

        await self._write(operation)
        return await self._get_annotation(annotation_id)

    async def get_analytics(
        self,
        *,
        channel: str | None = None,
        domain_id: str | None = None,
        model_name: str | None = None,
        annotation_code: str | None = None,
    ) -> QualityAnalytics:
        clauses: list[str] = []
        parameters: list[Any] = []
        for column, value in (("t.channel", channel), ("t.domain_id", domain_id), ("t.model_name", model_name)):
            if value:
                clauses.append(f"{column}=?")
                parameters.append(value)
        if annotation_code:
            clauses.append(
                "EXISTS(SELECT 1 FROM quality_annotations qa WHERE qa.turn_id=t.id AND qa.code=?)"
            )
            parameters.append(annotation_code)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        async with self._connect() as database:
            rows = await (await database.execute(
                f"""
                SELECT t.id, t.status, t.duration_ms,
                    (SELECT COUNT(*) FROM quality_tool_runs tr WHERE tr.turn_id=t.id) tool_count,
                    EXISTS(SELECT 1 FROM quality_citations c WHERE c.turn_id=t.id) has_citation,
                    EXISTS(SELECT 1 FROM quality_feedback f WHERE f.turn_id=t.id) has_feedback
                FROM quality_turns t {where}
                """,
                parameters,
            )).fetchall()
            issue_rows = await (await database.execute(
                f"""
                SELECT qa.code, COUNT(*) count
                FROM quality_annotations qa JOIN quality_turns t ON t.id=qa.turn_id
                {where} GROUP BY qa.code
                """,
                parameters,
            )).fetchall()
        total = len(rows)
        completed = [row for row in rows if row["status"] == "completed"]
        durations = sorted(float(row["duration_ms"]) for row in rows if row["duration_ms"] is not None)
        return QualityAnalytics(
            total_turns=total,
            completed_turns=len(completed),
            citation_coverage=(sum(int(row["has_citation"]) for row in completed) / len(completed)) if completed else 0.0,
            average_tool_calls=(sum(int(row["tool_count"]) for row in rows) / total) if total else 0.0,
            feedback_rate=(sum(int(row["has_feedback"]) for row in rows) / total) if total else 0.0,
            p50_duration_ms=self._percentile(durations, 0.5),
            p90_duration_ms=self._percentile(durations, 0.9),
            issue_counts={str(row["code"]): int(row["count"]) for row in issue_rows},
        )

    async def _get_annotation(self, annotation_id: str) -> QualityAnnotation:
        async with self._connect() as database:
            row = await (await database.execute(
                "SELECT * FROM quality_annotations WHERE id=?", (annotation_id,)
            )).fetchone()
        if row is None:
            raise QualityNotFoundError(annotation_id)
        return self._annotation_from_row(row)

    async def delete_feedback(
        self, *, turn_id: str, channel: str, user_id: str | None
    ) -> None:
        async def operation(database: aiosqlite.Connection) -> None:
            await database.execute(
                "DELETE FROM quality_feedback WHERE turn_id=? AND channel=? AND feedback_key=?",
                (turn_id, channel, user_id or "anonymous"),
            )

        await self._write(operation)

    async def delete_turn(self, turn_id: str) -> None:
        async def operation(database: aiosqlite.Connection) -> None:
            await database.execute("DELETE FROM quality_turns WHERE id=?", (turn_id,))

        await self._write(operation)

    async def create_eval_case(self, value: EvalCaseCreate) -> EvalCase:
        case_id = str(uuid4())
        now = self._now()

        async def operation(database: aiosqlite.Connection) -> None:
            await database.execute(
                """
                INSERT INTO eval_cases(
                    id, source_turn_id, name, question, knowledge_space_id,
                    domain_id, required_tools_json, required_citation_types_json,
                    required_facts_json, forbidden_facts_json, tags_json, enabled,
                    expected_behavior, max_latency_ms, max_tool_calls, max_citations,
                    turns_json, task_type, suite, priority, approval_state, version,
                    created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    case_id,
                    self._optional(value.source_turn_id),
                    value.name,
                    value.question,
                    value.knowledge_space_id,
                    self._optional(value.domain_id),
                    self._json(value.required_tools),
                    self._json(value.required_citation_types),
                    self._json(value.required_facts),
                    self._json(value.forbidden_facts),
                    self._json(value.tags),
                    int(value.enabled),
                    value.expected_behavior,
                    value.max_latency_ms,
                    value.max_tool_calls,
                    value.max_citations,
                    self._json(value.turns),
                    value.task_type,
                    value.suite,
                    value.priority,
                    value.approval_state,
                    1,
                    now,
                    now,
                ),
            )

        await self._write(operation)
        cases = await self.list_eval_cases()
        return next(item for item in cases if item.id == case_id)

    async def upsert_eval_case(self, case_id: str, value: EvalCaseCreate) -> EvalCase:
        """Create or replace a deterministic evaluation case without changing its ID."""
        if not case_id.strip():
            raise ValueError("case_id cannot be blank")
        now = self._now()

        async def operation(database: aiosqlite.Connection) -> None:
            await database.execute(
                """
                INSERT INTO eval_cases(
                    id, source_turn_id, name, question, knowledge_space_id,
                    domain_id, required_tools_json, required_citation_types_json,
                    required_facts_json, forbidden_facts_json, tags_json, enabled,
                    expected_behavior, max_latency_ms, max_tool_calls, max_citations,
                    turns_json, task_type, suite, priority, approval_state, version,
                    created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    source_turn_id=excluded.source_turn_id,
                    name=excluded.name,
                    question=excluded.question,
                    knowledge_space_id=excluded.knowledge_space_id,
                    domain_id=excluded.domain_id,
                    required_tools_json=excluded.required_tools_json,
                    required_citation_types_json=excluded.required_citation_types_json,
                    required_facts_json=excluded.required_facts_json,
                    forbidden_facts_json=excluded.forbidden_facts_json,
                    tags_json=excluded.tags_json,
                    enabled=excluded.enabled,
                    expected_behavior=excluded.expected_behavior,
                    max_latency_ms=excluded.max_latency_ms,
                    max_tool_calls=excluded.max_tool_calls,
                    max_citations=excluded.max_citations,
                    turns_json=excluded.turns_json,
                    task_type=excluded.task_type,
                    suite=excluded.suite,
                    priority=excluded.priority,
                    approval_state=excluded.approval_state,
                    version=eval_cases.version+1,
                    updated_at=excluded.updated_at
                """,
                (
                    case_id,
                    self._optional(value.source_turn_id),
                    value.name,
                    value.question,
                    value.knowledge_space_id,
                    self._optional(value.domain_id),
                    self._json(value.required_tools),
                    self._json(value.required_citation_types),
                    self._json(value.required_facts),
                    self._json(value.forbidden_facts),
                    self._json(value.tags),
                    int(value.enabled),
                    value.expected_behavior,
                    value.max_latency_ms,
                    value.max_tool_calls,
                    value.max_citations,
                    self._json(value.turns),
                    value.task_type,
                    value.suite,
                    value.priority,
                    value.approval_state,
                    1,
                    now,
                    now,
                ),
            )

        await self._write(operation)
        stored = await self.get_eval_case(case_id)
        if stored is None:
            raise RuntimeError("evaluation case could not be upserted")
        return stored

    async def list_eval_cases(self, *, enabled: bool | None = None) -> list[EvalCase]:
        query = "SELECT * FROM eval_cases"
        parameters: tuple[Any, ...] = ()
        if enabled is not None:
            query += " WHERE enabled=?"
            parameters = (int(enabled),)
        query += " ORDER BY created_at DESC"
        async with self._connect() as database:
            rows = await (await database.execute(query, parameters)).fetchall()
        return [self._eval_case_from_row(row) for row in rows]

    async def get_eval_case(self, case_id: str) -> EvalCase | None:
        async with self._connect() as database:
            row = await (
                await database.execute("SELECT * FROM eval_cases WHERE id=?", (case_id,))
            ).fetchone()
        return self._eval_case_from_row(row) if row else None

    async def update_eval_case(self, case_id: str, value: EvalCaseCreate) -> EvalCase:
        async def operation(database: aiosqlite.Connection) -> None:
            cursor = await database.execute(
                """
                UPDATE eval_cases
                SET name=?, question=?, knowledge_space_id=?, domain_id=?,
                    required_tools_json=?, required_citation_types_json=?,
                    required_facts_json=?, forbidden_facts_json=?, tags_json=?,
                    enabled=?, expected_behavior=?, max_latency_ms=?,
                    max_tool_calls=?, max_citations=?, turns_json=?, task_type=?,
                    suite=?, priority=?, approval_state=?, version=version+1, updated_at=?
                WHERE id=?
                """,
                (
                    value.name,
                    value.question,
                    value.knowledge_space_id,
                    self._optional(value.domain_id),
                    self._json(value.required_tools),
                    self._json(value.required_citation_types),
                    self._json(value.required_facts),
                    self._json(value.forbidden_facts),
                    self._json(value.tags),
                    int(value.enabled),
                    value.expected_behavior,
                    value.max_latency_ms,
                    value.max_tool_calls,
                    value.max_citations,
                    self._json(value.turns),
                    value.task_type,
                    value.suite,
                    value.priority,
                    value.approval_state,
                    self._now(),
                    case_id,
                ),
            )
            if cursor.rowcount == 0:
                raise QualityNotFoundError(case_id)

        await self._write(operation)
        updated = await self.get_eval_case(case_id)
        if updated is None:
            raise QualityNotFoundError(case_id)
        return updated

    async def delete_eval_case(self, case_id: str) -> None:
        async def operation(database: aiosqlite.Connection) -> None:
            await database.execute("DELETE FROM eval_cases WHERE id=?", (case_id,))

        await self._write(operation)

    async def create_eval_run(
        self,
        *,
        total_cases: int,
        application_version: str,
        provider: str,
        model_name: str,
        status: str = "running",
        case_ids: list[str] | None = None,
        config_snapshot: dict[str, Any] | None = None,
    ) -> EvalRun:
        run_id = str(uuid4())
        now = self._now()

        async def operation(database: aiosqlite.Connection) -> None:
            await database.execute(
                """
                INSERT INTO eval_runs(
                    id, status, application_version, provider, model_name,
                    total_cases, passed_cases, failed_cases, case_ids_json,
                    config_snapshot_json, cancel_requested, current_case,
                    created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, 0, 0, ?, ?, 0, 0, ?, ?)
                """,
                (
                    run_id, status, application_version, provider, model_name,
                    total_cases, self._json(case_ids or []),
                    self._json(self._sanitize_audit(config_snapshot or {})), now, now,
                ),
            )

        await self._write(operation)
        run = await self.get_eval_run(run_id)
        if run is None:
            raise RuntimeError("evaluation run could not be created")
        return run

    async def claim_next_eval_run(self) -> EvalRun | None:
        now = self._now()

        async def operation(database: aiosqlite.Connection) -> str | None:
            row = await (await database.execute(
                "SELECT id FROM eval_runs WHERE status='queued' ORDER BY created_at LIMIT 1"
            )).fetchone()
            if row is None:
                return None
            run_id = str(row["id"])
            cursor = await database.execute(
                "UPDATE eval_runs SET status='running', updated_at=? WHERE id=? AND status='queued'",
                (now, run_id),
            )
            return run_id if cursor.rowcount else None

        run_id = await self._write(operation)
        return await self.get_eval_run(run_id) if run_id else None

    async def request_eval_run_cancel(self, run_id: str) -> EvalRun:
        async def operation(database: aiosqlite.Connection) -> None:
            cursor = await database.execute(
                "UPDATE eval_runs SET cancel_requested=1, updated_at=? WHERE id=? AND status IN ('queued','running')",
                (self._now(), run_id),
            )
            if cursor.rowcount == 0:
                raise QualityNotFoundError(run_id)

        await self._write(operation)
        run = await self.get_eval_run(run_id)
        if run is None:
            raise QualityNotFoundError(run_id)
        return run

    async def update_eval_progress(self, run_id: str, current_case: int) -> None:
        async def operation(database: aiosqlite.Connection) -> None:
            await database.execute(
                "UPDATE eval_runs SET current_case=?, updated_at=? WHERE id=?",
                (current_case, self._now(), run_id),
            )

        await self._write(operation)

    async def mark_eval_run_cancelled(self, run_id: str) -> EvalRun:
        now = self._now()

        async def operation(database: aiosqlite.Connection) -> None:
            await database.execute(
                "UPDATE eval_runs SET status='cancelled', completed_at=?, updated_at=? WHERE id=?",
                (now, now, run_id),
            )

        await self._write(operation)
        run = await self.get_eval_run(run_id)
        if run is None:
            raise QualityNotFoundError(run_id)
        return run

    async def recover_eval_runs(self, stale_seconds: int = 300) -> int:
        cutoff = (datetime.now(UTC) - timedelta(seconds=stale_seconds)).isoformat()

        async def operation(database: aiosqlite.Connection) -> int:
            cursor = await database.execute(
                "UPDATE eval_runs SET status='queued', updated_at=? WHERE status='running' AND updated_at<?",
                (self._now(), cutoff),
            )
            return int(cursor.rowcount)

        return await self._write(operation)

    async def save_eval_result(
        self,
        *,
        run_id: str,
        case_id: str,
        status: str,
        answer: str | None,
        last_agent: str,
        tool_names: list[str],
        citation_types: list[str],
        duration_ms: float | None,
        checks: dict[str, bool],
        passed: bool,
        error_type: str | None = None,
        judge_score: float | None = None,
        judge: dict[str, Any] | None = None,
        failure_codes: list[str] | None = None,
        review_state: str = "not_required",
        case_snapshot: dict[str, Any] | None = None,
    ) -> EvalResult:
        result_id = str(uuid4())
        now = self._now()

        async def operation(database: aiosqlite.Connection) -> None:
            await database.execute(
                """
                INSERT INTO eval_results(
                    id, run_id, case_id, status, answer, last_agent,
                    tool_names_json, citation_types_json, duration_ms,
                    checks_json, passed, error_type, judge_score, judge_json,
                    failure_codes_json, review_state, case_snapshot_json, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(run_id, case_id) DO UPDATE SET
                    status=excluded.status,
                    answer=excluded.answer,
                    last_agent=excluded.last_agent,
                    tool_names_json=excluded.tool_names_json,
                    citation_types_json=excluded.citation_types_json,
                    duration_ms=excluded.duration_ms,
                    checks_json=excluded.checks_json,
                    passed=excluded.passed,
                    error_type=excluded.error_type,
                    judge_score=excluded.judge_score,
                    judge_json=excluded.judge_json,
                    failure_codes_json=excluded.failure_codes_json,
                    review_state=excluded.review_state,
                    case_snapshot_json=excluded.case_snapshot_json,
                    created_at=excluded.created_at
                """,
                (
                    result_id,
                    run_id,
                    case_id,
                    status,
                    answer,
                    last_agent,
                    self._json(tool_names),
                    self._json(citation_types),
                    duration_ms,
                    self._json(checks),
                    int(passed),
                    self._optional(error_type),
                    judge_score,
                    self._json(self._sanitize_audit(judge or {})),
                    self._json(failure_codes or []),
                    review_state,
                    self._json(self._sanitize_audit(case_snapshot or {})),
                    now,
                ),
            )

        await self._write(operation)
        results = await self.list_eval_results(run_id)
        return next(item for item in results if item.case_id == case_id)

    async def complete_eval_run(self, run_id: str) -> EvalRun:
        now = self._now()

        async def operation(database: aiosqlite.Connection) -> None:
            counts = await (
                await database.execute(
                    """
                    SELECT COUNT(*) total,
                           SUM(CASE WHEN passed=1 THEN 1 ELSE 0 END) passed
                    FROM eval_results WHERE run_id=?
                    """,
                    (run_id,),
                )
            ).fetchone()
            total = int(counts["total"] or 0)
            passed = int(counts["passed"] or 0)
            failed = total - passed
            status = "completed" if failed == 0 else "completed_with_failures"
            cursor = await database.execute(
                """
                UPDATE eval_runs
                SET status=?, passed_cases=?, failed_cases=?, completed_at=?, updated_at=?
                WHERE id=?
                """,
                (status, passed, failed, now, now, run_id),
            )
            if cursor.rowcount == 0:
                raise QualityNotFoundError(run_id)

        await self._write(operation)
        run = await self.get_eval_run(run_id)
        if run is None:
            raise QualityNotFoundError(run_id)
        return run

    async def get_eval_run(self, run_id: str) -> EvalRun | None:
        async with self._connect() as database:
            row = await (
                await database.execute("SELECT * FROM eval_runs WHERE id=?", (run_id,))
            ).fetchone()
        return self._eval_run_from_row(row) if row else None

    async def list_eval_runs(self) -> list[EvalRun]:
        async with self._connect() as database:
            rows = await (
                await database.execute("SELECT * FROM eval_runs ORDER BY created_at DESC")
            ).fetchall()
        return [self._eval_run_from_row(row) for row in rows]

    async def list_eval_results(self, run_id: str) -> list[EvalResult]:
        async with self._connect() as database:
            rows = await (
                await database.execute(
                    "SELECT * FROM eval_results WHERE run_id=? ORDER BY created_at",
                    (run_id,),
                )
            ).fetchall()
        return [self._eval_result_from_row(row) for row in rows]

    async def recover_stale_running(self, timeout_seconds: int) -> int:
        cutoff = (datetime.now(UTC) - timedelta(seconds=timeout_seconds)).isoformat()
        now = self._now()

        async def operation(database: aiosqlite.Connection) -> int:
            cursor = await database.execute(
                """
                UPDATE quality_turns
                SET status='interrupted', error_type='ServiceRestart',
                    updated_at=?, completed_at=?
                WHERE status='running' AND updated_at < ?
                """,
                (now, now, cutoff),
            )
            return int(cursor.rowcount)

        return await self._write(operation)

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[aiosqlite.Connection]:
        database = await aiosqlite.connect(self.database_path, timeout=5)
        database.row_factory = aiosqlite.Row
        await database.execute("PRAGMA foreign_keys=ON")
        await database.execute("PRAGMA busy_timeout=5000")
        try:
            yield database
        finally:
            await database.close()

    async def _write(
        self,
        operation: Callable[[aiosqlite.Connection], Coroutine[Any, Any, _T]],
    ) -> _T:
        for attempt in range(3):
            try:
                async with self._connect() as database:
                    await database.execute("BEGIN IMMEDIATE")
                    result = await operation(database)
                    await database.commit()
                    return result
            except aiosqlite.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 2:
                    raise
                await asyncio.sleep(0.05 * (attempt + 1))
        raise RuntimeError("unreachable")

    async def _turn_from_row(
        self,
        database: aiosqlite.Connection,
        row: aiosqlite.Row,
        *,
        details: bool,
    ) -> QualityTurn:
        tools: list[ToolRunSnapshot] = []
        citations: list[CitationSnapshot] = []
        feedback: list[FeedbackRecord] = []
        if details:
            tool_rows = await (
                await database.execute(
                    "SELECT * FROM quality_tool_runs WHERE turn_id=? ORDER BY sequence",
                    (row["id"],),
                )
            ).fetchall()
            tools = [
                ToolRunSnapshot(
                    tool_call_id=item["tool_call_id"],
                    tool_name=item["tool_name"],
                    agent_name=item["agent_name"],
                    status=item["status"],
                    duration_ms=item["duration_ms"],
                    arguments=json.loads(item["arguments_json"]),
                )
                for item in tool_rows
            ]
            citation_rows = await (
                await database.execute(
                    "SELECT * FROM quality_citations WHERE turn_id=? ORDER BY sequence",
                    (row["id"],),
                )
            ).fetchall()
            citations = [
                CitationSnapshot(
                    source_type=item["source_type"],
                    source_id=item["source_id"],
                    title=item["title"],
                    domain=item["domain"],
                    metadata=json.loads(item["metadata_json"]),
                )
                for item in citation_rows
            ]
            feedback_rows = await (
                await database.execute(
                    "SELECT * FROM quality_feedback WHERE turn_id=? ORDER BY created_at",
                    (row["id"],),
                )
            ).fetchall()
            feedback = [self._feedback_from_row(item) for item in feedback_rows]
        return QualityTurn(
            id=row["id"],
            run_id=row["run_id"],
            conversation_id=row["conversation_id"],
            channel=row["channel"],
            channel_message_id=row["channel_message_id"],
            channel_reply_message_id=row["channel_reply_message_id"],
            user_id=row["user_id"],
            user_name=row["user_name"],
            chat_id=row["chat_id"],
            question=row["question"],
            answer=row["answer"],
            knowledge_space_id=row["knowledge_space_id"],
            domain_id=row["domain_id"],
            status=row["status"],
            provider=row["provider"],
            model_name=row["model_name"],
            last_agent=row["last_agent"],
            application_version=row["application_version"],
            prompt_version=row["prompt_version"],
            duration_ms=row["duration_ms"],
            error_type=row["error_type"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
            tools=tools,
            citations=citations,
            feedback=feedback,
            routed_domains=json.loads(row["routed_domains_json"] or "[]"),
            specialists_used=json.loads(row["specialists_used_json"] or "[]"),
        )

    @staticmethod
    def _feedback_from_row(row: aiosqlite.Row) -> FeedbackRecord:
        return FeedbackRecord(
            id=row["id"],
            turn_id=row["turn_id"],
            channel=row["channel"],
            user_id=row["user_id"],
            user_name=row["user_name"],
            rating=row["rating"],
            reason=row["reason"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            reason_code=row["reason_code"],
        )

    @staticmethod
    def _span_from_row(row: aiosqlite.Row) -> QualitySpan:
        return QualitySpan(
            id=row["id"],
            turn_id=row["turn_id"],
            run_id=row["run_id"],
            kind=row["kind"],
            name=row["name"],
            status=row["status"],
            duration_ms=row["duration_ms"],
            input_tokens=int(row["input_tokens"]),
            output_tokens=int(row["output_tokens"]),
            total_tokens=int(row["total_tokens"]),
            metadata=json.loads(row["metadata_json"] or "{}"),
            created_at=row["created_at"],
        )

    @staticmethod
    def _annotation_from_row(row: aiosqlite.Row) -> QualityAnnotation:
        return QualityAnnotation(
            id=row["id"],
            turn_id=row["turn_id"],
            source=row["source"],
            code=row["code"],
            severity=row["severity"],
            confidence=float(row["confidence"]),
            details=json.loads(row["details_json"] or "{}"),
            review_status=row["review_status"],
            reviewer=row["reviewer"],
            reviewed_at=row["reviewed_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _percentile(values: list[float], quantile: float) -> float | None:
        if not values:
            return None
        if len(values) == 1:
            return values[0]
        position = (len(values) - 1) * quantile
        lower = int(position)
        upper = min(len(values) - 1, lower + 1)
        fraction = position - lower
        return values[lower] + (values[upper] - values[lower]) * fraction

    @staticmethod
    def _eval_case_from_row(row: aiosqlite.Row) -> EvalCase:
        return EvalCase(
            id=row["id"],
            source_turn_id=row["source_turn_id"],
            name=row["name"],
            question=row["question"],
            knowledge_space_id=row["knowledge_space_id"],
            domain_id=row["domain_id"],
            required_tools=json.loads(row["required_tools_json"]),
            required_citation_types=json.loads(row["required_citation_types_json"]),
            required_facts=json.loads(row["required_facts_json"]),
            forbidden_facts=json.loads(row["forbidden_facts_json"]),
            tags=json.loads(row["tags_json"]),
            enabled=bool(row["enabled"]),
            expected_behavior=str(row["expected_behavior"]),
            max_latency_ms=float(row["max_latency_ms"]),
            max_tool_calls=int(row["max_tool_calls"]),
            max_citations=int(row["max_citations"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            turns=json.loads(row["turns_json"] or "[]"),
            task_type=str(row["task_type"] or "unknown"),
            suite=str(row["suite"] or "routing-breadth"),
            priority=str(row["priority"] or "normal"),
            approval_state=str(row["approval_state"] or "candidate"),
            version=int(row["version"] or 1),
        )

    @staticmethod
    def _eval_run_from_row(row: aiosqlite.Row) -> EvalRun:
        return EvalRun(
            id=row["id"],
            status=row["status"],
            application_version=row["application_version"],
            provider=row["provider"],
            model_name=row["model_name"],
            total_cases=int(row["total_cases"]),
            passed_cases=int(row["passed_cases"]),
            failed_cases=int(row["failed_cases"]),
            created_at=row["created_at"],
            completed_at=row["completed_at"],
            case_ids=json.loads(row["case_ids_json"] or "[]"),
            config_snapshot=json.loads(row["config_snapshot_json"] or "{}"),
            cancel_requested=bool(row["cancel_requested"]),
            current_case=int(row["current_case"] or 0),
        )

    @staticmethod
    def _eval_result_from_row(row: aiosqlite.Row) -> EvalResult:
        return EvalResult(
            id=row["id"],
            run_id=row["run_id"],
            case_id=row["case_id"],
            status=row["status"],
            answer=row["answer"],
            last_agent=row["last_agent"],
            tool_names=json.loads(row["tool_names_json"]),
            citation_types=json.loads(row["citation_types_json"]),
            duration_ms=row["duration_ms"],
            checks=json.loads(row["checks_json"]),
            passed=bool(row["passed"]),
            error_type=row["error_type"],
            created_at=row["created_at"],
            judge_score=row["judge_score"],
            judge=json.loads(row["judge_json"] or "{}"),
            failure_codes=json.loads(row["failure_codes_json"] or "[]"),
            review_state=str(row["review_state"] or "not_required"),
            case_snapshot=json.loads(row["case_snapshot_json"] or "{}"),
        )

    @classmethod
    def _sanitize_audit(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): cls._sanitize_audit(item)
                for key, item in value.items()
                if str(key).lower() not in _BLOCKED_AUDIT_KEYS
                and not any(
                    blocked in str(key).lower()
                    for blocked in ("password", "secret", "token", "authorization")
                )
            }
        if isinstance(value, list):
            return [cls._sanitize_audit(item) for item in value[:100]]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

    @staticmethod
    def _token_hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @classmethod
    def _valid_token(cls, value: str, expected_hash: str) -> bool:
        return bool(value) and hmac.compare_digest(cls._token_hash(value), expected_hash)

    @staticmethod
    def _optional(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()
