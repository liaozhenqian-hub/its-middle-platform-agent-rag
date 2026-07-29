from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, AsyncIterator, Iterable
from uuid import uuid4

import aiosqlite

from knowledge.persistence.database import DatabaseResources
from knowledge.persistence.sqlite_compat import PostgresCompatConnection

from knowledge.memory.models import (
    Memory,
    MemoryCandidate,
    MemoryCandidateCreate,
    MemoryExtractionJob,
    ConversationSummary,
    ProceduralSpec,
    ProceduralStep,
    DomainPromotion,
)
from knowledge.memory.policy import MemoryPolicy
from knowledge.memory.migrations import apply_memory_migrations


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class MemoryRepository:
    _SCOPES = {"user", "conversation", "team", "domain", "global"}
    _TYPES = {
        "user_preference",
        "user_context",
        "episodic_memory",
        "decision_memory",
        "procedural_memory",
    }

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
            await db.execute("PRAGMA foreign_keys=ON")
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    scope_type TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    space_id TEXT NOT NULL,
                    domain_id TEXT,
                    memory_type TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    normalized_fact TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    source_turn_id TEXT,
                    source_citations_json TEXT NOT NULL DEFAULT '[]',
                    confidence REAL NOT NULL,
                    status TEXT NOT NULL,
                    valid_from TEXT NOT NULL,
                    valid_until TEXT,
                    last_used_at TEXT,
                    supersedes_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memories_namespace
                    ON memories(scope_type, owner_id, space_id, domain_id, status);
                CREATE INDEX IF NOT EXISTS idx_memories_expiry
                    ON memories(status, valid_until);
                CREATE TABLE IF NOT EXISTS memory_candidates (
                    id TEXT PRIMARY KEY,
                    scope_type TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    space_id TEXT NOT NULL,
                    domain_id TEXT,
                    memory_type TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    normalized_fact TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    source_turn_id TEXT,
                    source_citations_json TEXT NOT NULL DEFAULT '[]',
                    confidence REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'candidate',
                    expires_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memory_candidates_review
                    ON memory_candidates(status, created_at);
                CREATE TABLE IF NOT EXISTS memory_audit_events (
                    id TEXT PRIMARY KEY,
                    memory_id TEXT,
                    candidate_id TEXT,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory_extraction_jobs (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    space_id TEXT NOT NULL,
                    domain_id TEXT,
                    channel TEXT NOT NULL,
                    question TEXT NOT NULL,
                    answer TEXT,
                    source_turn_id TEXT,
                    source_citations_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'queued',
                    attempt INTEGER NOT NULL DEFAULT 0,
                    worker_id TEXT,
                    error_type TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memory_extraction_jobs_status
                    ON memory_extraction_jobs(status, created_at);
                CREATE TABLE IF NOT EXISTS conversation_memory_summaries (
                    conversation_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    space_id TEXT NOT NULL,
                    domain_id TEXT,
                    summary TEXT NOT NULL,
                    goals_json TEXT NOT NULL DEFAULT '[]',
                    confirmed_facts_json TEXT NOT NULL DEFAULT '[]',
                    unresolved_items_json TEXT NOT NULL DEFAULT '[]',
                    preferences_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            await apply_memory_migrations(db)
            await db.commit()

    async def upsert_conversation_summary(self, value: ConversationSummary) -> ConversationSummary:
        if not value.conversation_id or not value.user_id or not value.space_id:
            raise ValueError("conversation summary namespace is required")
        now = _now()
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO conversation_memory_summaries(
                    conversation_id,user_id,space_id,domain_id,summary,
                    goals_json,confirmed_facts_json,unresolved_items_json,
                    preferences_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(conversation_id) DO UPDATE SET
                    user_id=excluded.user_id, space_id=excluded.space_id,
                    domain_id=excluded.domain_id, summary=excluded.summary,
                    goals_json=excluded.goals_json,
                    confirmed_facts_json=excluded.confirmed_facts_json,
                    unresolved_items_json=excluded.unresolved_items_json,
                    preferences_json=excluded.preferences_json,
                    updated_at=excluded.updated_at
                """,
                (
                    value.conversation_id, value.user_id, value.space_id,
                    value.domain_id, value.summary,
                    json.dumps(value.goals, ensure_ascii=False),
                    json.dumps(value.confirmed_facts, ensure_ascii=False),
                    json.dumps(value.unresolved_items, ensure_ascii=False),
                    json.dumps(value.preferences, ensure_ascii=False),
                    _iso(now), _iso(now),
                ),
            )
            await db.commit()
        return await self.get_conversation_summary(value.conversation_id)

    async def get_conversation_summary(self, conversation_id: str) -> ConversationSummary | None:
        async with self._connect() as db:
            row = await (await db.execute(
                "SELECT * FROM conversation_memory_summaries WHERE conversation_id=?",
                (conversation_id,),
            )).fetchone()
        if row is None:
            return None
        return ConversationSummary(
            conversation_id=row["conversation_id"], user_id=row["user_id"],
            space_id=row["space_id"], domain_id=row["domain_id"],
            summary=row["summary"], goals=tuple(json.loads(row["goals_json"])),
            confirmed_facts=tuple(json.loads(row["confirmed_facts_json"])),
            unresolved_items=tuple(json.loads(row["unresolved_items_json"])),
            preferences=tuple(json.loads(row["preferences_json"])),
            created_at=_parse(row["created_at"]), updated_at=_parse(row["updated_at"]),
        )

    async def delete_conversation_summary(self, conversation_id: str) -> bool:
        async with self._connect() as db:
            cursor = await db.execute(
                "DELETE FROM conversation_memory_summaries WHERE conversation_id=?",
                (conversation_id,),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def enqueue_extraction(
        self,
        *,
        user_id: str,
        conversation_id: str,
        space_id: str,
        domain_id: str | None,
        channel: str,
        question: str,
        answer: str | None,
        source_turn_id: str | None,
        source_citations: tuple[str, ...],
        policy: MemoryPolicy | None = None,
    ) -> MemoryExtractionJob:
        active_policy = policy or MemoryPolicy()
        normalized_question = " ".join(question.split())[:1800]
        normalized_answer = " ".join((answer or "").split())[:1800] or None
        if (
            not user_id.strip()
            or not conversation_id.strip()
            or not space_id.strip()
            or not active_policy.allows_text(normalized_question)
            or (normalized_answer is not None and not active_policy.allows_text(normalized_answer))
        ):
            raise ValueError("unsafe or incomplete memory extraction payload")
        job_id = str(uuid4())
        now = _now()
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO memory_extraction_jobs(
                    id,user_id,conversation_id,space_id,domain_id,channel,
                    question,answer,source_turn_id,source_citations_json,
                    status,attempt,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?, 'queued',0,?,?)
                """,
                (
                    job_id, user_id.strip(), conversation_id.strip(), space_id.strip(),
                    domain_id, channel[:50], normalized_question, normalized_answer,
                    source_turn_id, json.dumps(source_citations, ensure_ascii=False),
                    _iso(now), _iso(now),
                ),
            )
            await db.commit()
        return await self.get_extraction_job(job_id)

    async def claim_extraction_job(self, worker_id: str) -> MemoryExtractionJob | None:
        now = _now()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            row = await (await db.execute(
                "SELECT id FROM memory_extraction_jobs WHERE status='queued' ORDER BY created_at LIMIT 1"
            )).fetchone()
            if row is None:
                await db.commit()
                return None
            await db.execute(
                """
                UPDATE memory_extraction_jobs
                SET status='running', attempt=attempt+1, worker_id=?, updated_at=?
                WHERE id=? AND status='queued'
                """,
                (worker_id, _iso(now), row["id"]),
            )
            await db.commit()
        return await self.get_extraction_job(str(row["id"]))

    async def get_extraction_job(self, job_id: str) -> MemoryExtractionJob:
        async with self._connect() as db:
            row = await (await db.execute(
                "SELECT * FROM memory_extraction_jobs WHERE id=?", (job_id,)
            )).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self._extraction_job(row)

    async def complete_extraction_job(self, job_id: str) -> None:
        async with self._connect() as db:
            await db.execute(
                "UPDATE memory_extraction_jobs SET status='succeeded', error_type=NULL, updated_at=? WHERE id=?",
                (_iso(_now()), job_id),
            )
            await db.commit()

    async def fail_extraction_job(
        self, job_id: str, *, error_type: str, max_attempts: int = 3
    ) -> None:
        job = await self.get_extraction_job(job_id)
        status = "queued" if job.attempt < max_attempts else "failed"
        async with self._connect() as db:
            await db.execute(
                "UPDATE memory_extraction_jobs SET status=?, worker_id=NULL, error_type=?, updated_at=? WHERE id=?",
                (status, error_type[:200], _iso(_now()), job_id),
            )
            await db.commit()

    async def requeue_extraction_job(self, job_id: str, *, worker_id: str) -> bool:
        async with self._connect() as db:
            cursor = await db.execute(
                """
                UPDATE memory_extraction_jobs
                SET status='queued', worker_id=NULL, updated_at=?
                WHERE id=? AND status='running' AND worker_id=?
                """,
                (_iso(_now()), job_id, worker_id),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def recover_stale_extraction_jobs(self, stale_seconds: int) -> int:
        cutoff = _now().timestamp() - max(0, stale_seconds)
        cutoff_iso = datetime.fromtimestamp(cutoff, UTC).isoformat()
        async with self._connect() as db:
            cursor = await db.execute(
                """
                UPDATE memory_extraction_jobs
                SET status='queued', worker_id=NULL, updated_at=?
                WHERE status='running' AND updated_at<=?
                """,
                (_iso(_now()), cutoff_iso),
            )
            await db.commit()
            return cursor.rowcount

    async def cleanup_terminal_records(self, *, apply: bool = False) -> dict[str, int]:
        async with self._connect() as db:
            counts = {
                "memories": int((await (await db.execute(
                    "SELECT COUNT(*) AS count FROM memories WHERE status IN ('deleted','expired')"
                )).fetchone())["count"]),
                "candidates": int((await (await db.execute(
                    "SELECT COUNT(*) AS count FROM memory_candidates WHERE status IN ('approved','rejected','expired')"
                )).fetchone())["count"]),
                "jobs": int((await (await db.execute(
                    "SELECT COUNT(*) AS count FROM memory_extraction_jobs WHERE status IN ('succeeded','failed')"
                )).fetchone())["count"]),
            }
            if apply:
                await db.execute("DELETE FROM memories WHERE status IN ('deleted','expired')")
                await db.execute(
                    "DELETE FROM memory_candidates WHERE status IN ('approved','rejected','expired')"
                )
                await db.execute(
                    "DELETE FROM memory_extraction_jobs WHERE status IN ('succeeded','failed')"
                )
                await db.commit()
            return counts

    @classmethod
    def _validate_create(cls, value: MemoryCandidateCreate) -> None:
        if value.scope_type not in cls._SCOPES:
            raise ValueError("unsupported memory scope")
        if value.memory_type not in cls._TYPES:
            raise ValueError("unsupported memory type")
        if not value.owner_id.strip() or not value.space_id.strip():
            raise ValueError("memory owner and space are required")
        if not value.subject.strip() or not value.normalized_fact.strip():
            raise ValueError("memory subject and fact are required")
        if not 0 <= value.confidence <= 1:
            raise ValueError("memory confidence must be between 0 and 1")

    async def create_candidate(self, value: MemoryCandidateCreate) -> MemoryCandidate:
        self._validate_create(value)
        memory_id = value.id or str(uuid4())
        now = _now()
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO memory_candidates(
                    id, scope_type, owner_id, space_id, domain_id, memory_type,
                    subject, normalized_fact, summary, source_turn_id,
                    source_citations_json, confidence, status, expires_at,
                    created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    memory_id, value.scope_type, value.owner_id, value.space_id,
                    value.domain_id, value.memory_type, value.subject.strip(),
                    value.normalized_fact.strip(), value.summary.strip(),
                    value.source_turn_id, json.dumps(value.source_citations, ensure_ascii=False),
                    value.confidence, "candidate", _iso(value.expires_at),
                    _iso(now), _iso(now),
                ),
            )
            await db.commit()
        return await self.get_candidate(memory_id)

    async def get_candidate(self, candidate_id: str) -> MemoryCandidate:
        async with self._connect() as db:
            row = await (await db.execute(
                "SELECT * FROM memory_candidates WHERE id=?", (candidate_id,)
            )).fetchone()
        if row is None:
            raise KeyError(candidate_id)
        return self._candidate(row)

    async def approve_candidate(
        self,
        candidate_id: str,
        *,
        actor: str = "admin",
        valid_until: datetime | None = None,
    ) -> Memory:
        candidate = await self.get_candidate(candidate_id)
        if candidate.status != "candidate":
            raise ValueError("memory candidate is no longer pending")
        now = _now()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            old_rows = await (await db.execute(
                """
                SELECT id FROM memories
                WHERE scope_type=? AND owner_id=? AND space_id=?
                  AND COALESCE(domain_id,'')=COALESCE(?, '')
                  AND memory_type=? AND subject=? AND status='confirmed'
                """,
                (
                    candidate.scope_type, candidate.owner_id, candidate.space_id,
                    candidate.domain_id, candidate.memory_type, candidate.subject,
                ),
            )).fetchall()
            await db.execute(
                """
                UPDATE memories SET status='expired', updated_at=?
                WHERE scope_type=? AND owner_id=? AND space_id=?
                  AND COALESCE(domain_id,'')=COALESCE(?, '')
                  AND memory_type=? AND subject=? AND status='confirmed'
                """,
                (
                    _iso(now), candidate.scope_type, candidate.owner_id,
                    candidate.space_id, candidate.domain_id, candidate.memory_type,
                    candidate.subject,
                ),
            )
            await db.execute(
                """
                INSERT INTO memories(
                    id, scope_type, owner_id, space_id, domain_id, memory_type,
                    subject, normalized_fact, summary, source_turn_id,
                    source_citations_json, confidence, status, valid_from,
                    valid_until, last_used_at, supersedes_id, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    candidate.id, candidate.scope_type, candidate.owner_id,
                    candidate.space_id, candidate.domain_id, candidate.memory_type,
                    candidate.subject, candidate.normalized_fact, candidate.summary,
                    candidate.source_turn_id, json.dumps(candidate.source_citations, ensure_ascii=False),
                    candidate.confidence, "confirmed", _iso(now), _iso(valid_until or candidate.expires_at),
                    None, old_rows[0]["id"] if old_rows else None, _iso(now), _iso(now),
                ),
            )
            await db.execute(
                "UPDATE memory_candidates SET status='approved', updated_at=? WHERE id=?",
                (_iso(now), candidate.id),
            )
            await self._audit_db(
                db, memory_id=candidate.id, candidate_id=candidate.id,
                actor=actor, action="approve", now=now,
            )
            await db.commit()
        return await self.get_memory(candidate.id)

    async def reject_candidate(self, candidate_id: str, *, actor: str = "admin") -> MemoryCandidate:
        candidate = await self.get_candidate(candidate_id)
        if candidate.status != "candidate":
            raise ValueError("memory candidate is no longer pending")
        now = _now()
        async with self._connect() as db:
            await db.execute(
                "UPDATE memory_candidates SET status='rejected', updated_at=? WHERE id=?",
                (_iso(now), candidate_id),
            )
            await self._audit_db(
                db, candidate_id=candidate_id, actor=actor, action="reject", now=now
            )
            await db.commit()
        return await self.get_candidate(candidate_id)

    async def list_candidates(
        self,
        *,
        status: str | None = None,
        scope_type: str | None = None,
        owner_id: str | None = None,
        domain_id: str | None = None,
        memory_type: str | None = None,
        limit: int = 100,
    ) -> list[MemoryCandidate]:
        sql = "SELECT * FROM memory_candidates"
        values: list[Any] = []
        clauses: list[str] = []
        for key, value in (
            ("status", status), ("scope_type", scope_type),
            ("owner_id", owner_id), ("domain_id", domain_id),
            ("memory_type", memory_type),
        ):
            if value is not None:
                clauses.append(f"{key}=?")
                values.append(value)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT ?"
        values.append(max(1, min(limit, 500)))
        async with self._connect() as db:
            rows = await (await db.execute(sql, values)).fetchall()
        return [self._candidate(row) for row in rows]

    async def list_due_user_candidates(
        self,
        cutoff: datetime,
        *,
        memory_types: tuple[str, ...] = ("user_preference", "user_context"),
        limit: int = 100,
    ) -> list[MemoryCandidate]:
        bounded_limit = max(1, min(limit, 1000))
        allowed_types = tuple(item for item in memory_types if item in self._TYPES)
        if not allowed_types:
            return []
        placeholders = ",".join("?" for _ in allowed_types)
        async with self._connect() as db:
            rows = await (await db.execute(
                f"""
                SELECT * FROM memory_candidates
                WHERE status='candidate'
                  AND scope_type='user'
                  AND created_at<=?
                  AND memory_type IN ({placeholders})
                ORDER BY created_at ASC, id ASC
                LIMIT ?
                """,
                (_iso(cutoff), *allowed_types, bounded_limit),
            )).fetchall()
        return [self._candidate(row) for row in rows]

    async def get_memory(self, memory_id: str) -> Memory | None:
        async with self._connect() as db:
            row = await (await db.execute(
                "SELECT * FROM memories WHERE id=? AND status!='deleted'", (memory_id,)
            )).fetchone()
        return self._memory(row) if row else None

    async def list_memories(
        self,
        *,
        scope_type: str | None = None,
        owner_id: str | None = None,
        space_id: str | None = None,
        domain_id: str | None = None,
        statuses: Iterable[str] = ("confirmed",),
        limit: int = 100,
    ) -> list[Memory]:
        clauses = ["status IN (" + ",".join("?" for _ in statuses) + ")", "status!='deleted'"]
        values: list[Any] = list(statuses)
        for key, value in (("scope_type", scope_type), ("owner_id", owner_id), ("space_id", space_id), ("domain_id", domain_id)):
            if value is not None:
                clauses.append(f"{key}=?")
                values.append(value)
        values.append(max(1, min(limit, 500)))
        async with self._connect() as db:
            rows = await (await db.execute(
                f"SELECT * FROM memories WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC LIMIT ?",
                values,
            )).fetchall()
        return [self._memory(row) for row in rows]

    async def personal_memory_statistics(self) -> dict[str, dict[str, int]]:
        output: dict[str, dict[str, int]] = {
            "candidate": {}, "confirmed": {}, "rejected": {}, "deleted": {},
        }
        async with self._connect() as db:
            candidate_rows = await (await db.execute(
                """
                SELECT status,memory_type,COUNT(*) AS count
                FROM memory_candidates WHERE scope_type='user'
                GROUP BY status,memory_type
                """
            )).fetchall()
            memory_rows = await (await db.execute(
                """
                SELECT status,memory_type,COUNT(*) AS count
                FROM memories WHERE scope_type='user'
                GROUP BY status,memory_type
                """
            )).fetchall()
        for row in candidate_rows:
            status = "candidate" if row["status"] == "candidate" else str(row["status"])
            if status in output:
                output[status][str(row["memory_type"])] = int(row["count"])
        for row in memory_rows:
            status = str(row["status"])
            if status in output:
                output[status][str(row["memory_type"])] = int(row["count"])
        return output

    async def upsert_procedural_spec(
        self, record_id: str, spec: ProceduralSpec
    ) -> None:
        from knowledge.memory.procedures import ProceduralMemoryValidator

        validated = ProceduralMemoryValidator().validate(spec)
        now = _iso(_now())
        steps = [
            {
                "capability": item.capability,
                "purpose": item.purpose,
                "required_inputs": list(item.required_inputs),
                "produced_signals": list(item.produced_signals),
                "next_condition": item.next_condition,
            }
            for item in validated.steps
        ]
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO memory_procedural_specs(
                    record_id,task_type,procedure_version,trigger_conditions_json,
                    required_inputs_json,environment_constraints_json,
                    branch_constraints_json,steps_json,allowed_tools_json,
                    minimum_evidence_grade,stop_conditions_json,fallback_actions_json,
                    expected_output_json,validation_steps_json,success_count,failure_count,
                    last_executed_at,reviewed_by,reviewed_at,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(record_id) DO UPDATE SET
                    task_type=excluded.task_type,
                    procedure_version=excluded.procedure_version,
                    trigger_conditions_json=excluded.trigger_conditions_json,
                    required_inputs_json=excluded.required_inputs_json,
                    environment_constraints_json=excluded.environment_constraints_json,
                    branch_constraints_json=excluded.branch_constraints_json,
                    steps_json=excluded.steps_json,allowed_tools_json=excluded.allowed_tools_json,
                    minimum_evidence_grade=excluded.minimum_evidence_grade,
                    stop_conditions_json=excluded.stop_conditions_json,
                    fallback_actions_json=excluded.fallback_actions_json,
                    expected_output_json=excluded.expected_output_json,
                    validation_steps_json=excluded.validation_steps_json,
                    updated_at=excluded.updated_at
                """,
                (
                    record_id, validated.task_type, validated.procedure_version,
                    json.dumps(validated.trigger_conditions, ensure_ascii=False),
                    json.dumps(validated.required_inputs, ensure_ascii=False),
                    json.dumps(validated.environment_constraints, ensure_ascii=False),
                    json.dumps(validated.branch_constraints, ensure_ascii=False),
                    json.dumps(steps, ensure_ascii=False),
                    json.dumps(validated.allowed_tools, ensure_ascii=False),
                    validated.minimum_evidence_grade,
                    json.dumps(validated.stop_conditions, ensure_ascii=False),
                    json.dumps(validated.fallback_actions, ensure_ascii=False),
                    json.dumps(validated.expected_output, ensure_ascii=False),
                    json.dumps(validated.validation_steps, ensure_ascii=False),
                    validated.success_count, validated.failure_count,
                    _iso(validated.last_executed_at), validated.reviewed_by,
                    _iso(validated.reviewed_at), now, now,
                ),
            )
            await db.commit()

    async def get_procedural_spec(self, record_id: str) -> ProceduralSpec | None:
        async with self._connect() as db:
            row = await (await db.execute(
                "SELECT * FROM memory_procedural_specs WHERE record_id=?", (record_id,)
            )).fetchone()
        if row is None:
            return None
        steps = tuple(
            ProceduralStep(
                capability=item["capability"], purpose=item["purpose"],
                required_inputs=tuple(item.get("required_inputs") or ()),
                produced_signals=tuple(item.get("produced_signals") or ()),
                next_condition=item.get("next_condition"),
            )
            for item in json.loads(row["steps_json"])
        )
        return ProceduralSpec(
            task_type=row["task_type"], procedure_version=int(row["procedure_version"]),
            trigger_conditions=tuple(json.loads(row["trigger_conditions_json"])),
            required_inputs=tuple(json.loads(row["required_inputs_json"])),
            environment_constraints=tuple(json.loads(row["environment_constraints_json"])),
            branch_constraints=tuple(json.loads(row["branch_constraints_json"])),
            steps=steps, allowed_tools=tuple(json.loads(row["allowed_tools_json"])),
            minimum_evidence_grade=row["minimum_evidence_grade"],
            stop_conditions=tuple(json.loads(row["stop_conditions_json"])),
            fallback_actions=tuple(json.loads(row["fallback_actions_json"])),
            expected_output=tuple(json.loads(row["expected_output_json"])),
            validation_steps=tuple(json.loads(row["validation_steps_json"])),
            success_count=int(row["success_count"]), failure_count=int(row["failure_count"]),
            last_executed_at=_parse(row["last_executed_at"]), reviewed_by=row["reviewed_by"],
            reviewed_at=_parse(row["reviewed_at"]),
        )

    async def list_matching_procedures(
        self, *, owner_id: str, domain_id: str | None, task_type: str,
        environment: str, branch: str, limit: int = 3,
    ) -> list[tuple[Memory, ProceduralSpec]]:
        async with self._connect() as db:
            rows = await (await db.execute(
                """
                SELECT m.id FROM memories m
                JOIN memory_procedural_specs p ON p.record_id=m.id
                WHERE m.status='confirmed' AND m.memory_type='procedural_memory'
                  AND m.scope_type='user' AND m.owner_id=?
                  AND COALESCE(m.domain_id,'')=COALESCE(?, '')
                  AND COALESCE(m.review_state,'approved')='approved'
                  AND p.task_type=?
                ORDER BY m.updated_at DESC LIMIT ?
                """,
                (owner_id, domain_id, task_type, max(1, min(limit * 4, 40))),
            )).fetchall()
        output: list[tuple[Memory, ProceduralSpec]] = []
        for row in rows:
            memory = await self.get_memory(str(row["id"]))
            spec = await self.get_procedural_spec(str(row["id"]))
            if (
                memory is not None and spec is not None
                and environment in spec.environment_constraints
                and branch in spec.branch_constraints
            ):
                output.append((memory, spec))
        return output[: max(1, min(limit, 10))]

    async def create_domain_promotion(
        self, *, source_memory_id: str, target_candidate_id: str,
        target_domain_id: str, public_summary: str, requested_by: str,
        valid_until: datetime | None,
    ) -> DomainPromotion:
        promotion_id = str(uuid4())
        now = _now()
        async with self._connect() as db:
            duplicate = await (await db.execute(
                """
                SELECT id FROM memory_domain_promotions
                WHERE source_memory_id=? AND target_domain_id=? AND state='pending'
                """,
                (source_memory_id, target_domain_id),
            )).fetchone()
            if duplicate is not None:
                raise ValueError("domain promotion already pending")
            await db.execute(
                """
                INSERT INTO memory_domain_promotions(
                    id,source_memory_id,target_candidate_id,target_domain_id,
                    public_summary,state,requested_by,reviewed_by,reviewed_at,
                    valid_until,created_at,updated_at
                ) VALUES(?,?,?,?,?,'pending',?,NULL,NULL,?,?,?)
                """,
                (
                    promotion_id, source_memory_id, target_candidate_id,
                    target_domain_id, public_summary, requested_by,
                    _iso(valid_until), _iso(now), _iso(now),
                ),
            )
            await db.commit()
        return await self.get_domain_promotion(promotion_id)

    async def get_domain_promotion(self, promotion_id: str) -> DomainPromotion:
        async with self._connect() as db:
            row = await (await db.execute(
                "SELECT * FROM memory_domain_promotions WHERE id=?", (promotion_id,)
            )).fetchone()
        if row is None:
            raise KeyError(promotion_id)
        return DomainPromotion(
            id=row["id"], source_memory_id=row["source_memory_id"],
            target_candidate_id=row["target_candidate_id"],
            target_domain_id=row["target_domain_id"], public_summary=row["public_summary"],
            state=row["state"], requested_by=row["requested_by"],
            reviewed_by=row["reviewed_by"], reviewed_at=_parse(row["reviewed_at"]),
            valid_until=_parse(row["valid_until"]), created_at=_parse(row["created_at"]),
            updated_at=_parse(row["updated_at"]),
        )

    async def list_domain_promotions(self, state: str = "pending") -> list[DomainPromotion]:
        async with self._connect() as db:
            rows = await (await db.execute(
                "SELECT id FROM memory_domain_promotions WHERE state=? ORDER BY created_at DESC",
                (state,),
            )).fetchall()
        return [await self.get_domain_promotion(str(row["id"])) for row in rows]

    async def review_domain_promotion(
        self, promotion_id: str, *, state: str, reviewed_by: str
    ) -> DomainPromotion:
        if state not in {"approved", "rejected"}:
            raise ValueError("invalid promotion review state")
        now = _now()
        async with self._connect() as db:
            cursor = await db.execute(
                """
                UPDATE memory_domain_promotions
                SET state=?,reviewed_by=?,reviewed_at=?,updated_at=?
                WHERE id=? AND state='pending'
                """,
                (state, reviewed_by, _iso(now), _iso(now), promotion_id),
            )
            await db.commit()
        if cursor.rowcount != 1:
            raise ValueError("domain promotion is no longer pending")
        return await self.get_domain_promotion(promotion_id)

    async def record_memory_conflict(
        self, memory_id: str, reason_code: str, *, threshold: int
    ) -> int:
        from knowledge.memory.conflicts import validate_conflict_reason

        reason = validate_conflict_reason(reason_code)
        now = _now()
        async with self._connect() as db:
            if await (await db.execute(
                "SELECT id FROM memories WHERE id=? AND status='confirmed'", (memory_id,)
            )).fetchone() is None:
                raise KeyError(memory_id)
            await db.execute(
                "INSERT INTO memory_conflicts(id,memory_id,reason_code,resolved,created_at) VALUES(?,?,?,?,?)",
                (str(uuid4()), memory_id, reason, 0, _iso(now)),
            )
            count_row = await (await db.execute(
                "SELECT COUNT(*) AS count FROM memory_conflicts WHERE memory_id=? AND resolved=0",
                (memory_id,),
            )).fetchone()
            count = int(count_row["count"])
            if count >= max(1, threshold):
                await db.execute(
                    "UPDATE memories SET review_state='review_required',review_reason=?,updated_at=? WHERE id=?",
                    (reason, _iso(now), memory_id),
                )
            await self._audit_db(
                db, memory_id=memory_id, actor="system:conflict",
                action="conflict", now=now,
            )
            await db.commit()
        return count

    async def enqueue_index_repair(
        self, memory_id: str, operation: str, error_type: str | None = None
    ) -> None:
        if operation not in {"upsert", "delete"}:
            raise ValueError("unsupported index repair operation")
        now = _iso(_now())
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO memory_index_repairs(
                    memory_id,operation,attempt,last_error_type,created_at,updated_at
                ) VALUES(?,?,0,?,?,?)
                ON CONFLICT(memory_id) DO UPDATE SET
                    operation=excluded.operation,last_error_type=excluded.last_error_type,
                    updated_at=excluded.updated_at
                """,
                (memory_id, operation, error_type, now, now),
            )
            await db.commit()

    async def list_index_repairs(self, limit: int = 100) -> list[tuple[str, str]]:
        async with self._connect() as db:
            rows = await (await db.execute(
                "SELECT memory_id,operation FROM memory_index_repairs ORDER BY created_at LIMIT ?",
                (max(1, min(limit, 500)),),
            )).fetchall()
        return [(str(row["memory_id"]), str(row["operation"])) for row in rows]

    async def complete_index_repair(self, memory_id: str) -> None:
        async with self._connect() as db:
            await db.execute("DELETE FROM memory_index_repairs WHERE memory_id=?", (memory_id,))
            await db.commit()

    async def preview_user_owner_merge(
        self, source_owner_id: str, target_owner_id: str
    ) -> dict[str, int]:
        source_owner_id = source_owner_id.strip()
        target_owner_id = target_owner_id.strip()
        if not source_owner_id or not target_owner_id:
            raise ValueError("source and target owner are required")
        async with self._connect() as db:
            source_rows = await (
                await db.execute(
                    """
                    SELECT * FROM memories
                    WHERE scope_type='user' AND owner_id=? AND status='confirmed'
                    """,
                    (source_owner_id,),
                )
            ).fetchall()
            candidate_row = await (
                await db.execute(
                    """
                    SELECT COUNT(*) AS count FROM memory_candidates
                    WHERE scope_type='user' AND owner_id=? AND status='candidate'
                    """,
                    (source_owner_id,),
                )
            ).fetchone()
            duplicates = 0
            conflicts = 0
            for row in source_rows:
                classification = await self._classify_merge_memory(
                    db, row, target_owner_id
                )
                duplicates += classification == "duplicate"
                conflicts += classification == "conflict"
        return {
            "memories": len(source_rows),
            "candidates": int(candidate_row["count"]),
            "duplicates": int(duplicates),
            "conflicts": int(conflicts),
            "unique": len(source_rows) - int(duplicates) - int(conflicts),
        }

    async def merge_user_owner(
        self, source_owner_id: str, target_owner_id: str
    ) -> dict[str, int]:
        source_owner_id = source_owner_id.strip()
        target_owner_id = target_owner_id.strip()
        if not source_owner_id or not target_owner_id:
            raise ValueError("source and target owner are required")
        counts = {
            "moved_memories": 0,
            "deduplicated_memories": 0,
            "conflicting_memories": 0,
            "moved_candidates": 0,
            "conversation_summaries": 0,
            "extraction_jobs": 0,
        }
        now = _now()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            source_rows = await (
                await db.execute(
                    """
                    SELECT * FROM memories
                    WHERE scope_type='user' AND owner_id=? AND status='confirmed'
                    """,
                    (source_owner_id,),
                )
            ).fetchall()
            for row in source_rows:
                classification = await self._classify_merge_memory(
                    db, row, target_owner_id
                )
                if classification == "unique":
                    await db.execute(
                        "UPDATE memories SET owner_id=?,updated_at=? WHERE id=?",
                        (target_owner_id, _iso(now), row["id"]),
                    )
                    counts["moved_memories"] += 1
                elif classification == "duplicate":
                    await db.execute(
                        "UPDATE memories SET status='deleted',updated_at=? WHERE id=?",
                        (_iso(now), row["id"]),
                    )
                    counts["deduplicated_memories"] += 1
                else:
                    candidate_id = str(uuid4())
                    await db.execute(
                        """
                        INSERT INTO memory_candidates(
                            id,scope_type,owner_id,space_id,domain_id,memory_type,
                            subject,normalized_fact,summary,source_turn_id,
                            source_citations_json,confidence,status,expires_at,
                            created_at,updated_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'candidate', ?,?,?)
                        """,
                        (
                            candidate_id,
                            row["scope_type"],
                            target_owner_id,
                            row["space_id"],
                            row["domain_id"],
                            row["memory_type"],
                            row["subject"],
                            row["normalized_fact"],
                            row["summary"],
                            row["source_turn_id"],
                            row["source_citations_json"],
                            row["confidence"],
                            row["valid_until"],
                            _iso(now),
                            _iso(now),
                        ),
                    )
                    await db.execute(
                        "UPDATE memories SET status='deleted',updated_at=? WHERE id=?",
                        (_iso(now), row["id"]),
                    )
                    counts["conflicting_memories"] += 1

            candidate_cursor = await db.execute(
                """
                UPDATE memory_candidates SET owner_id=?,updated_at=?
                WHERE scope_type='user' AND owner_id=? AND status='candidate'
                """,
                (target_owner_id, _iso(now), source_owner_id),
            )
            counts["moved_candidates"] = candidate_cursor.rowcount
            summary_cursor = await db.execute(
                """
                UPDATE conversation_memory_summaries SET user_id=?,updated_at=?
                WHERE user_id=?
                """,
                (target_owner_id, _iso(now), source_owner_id),
            )
            counts["conversation_summaries"] = summary_cursor.rowcount
            jobs_cursor = await db.execute(
                """
                UPDATE memory_extraction_jobs SET user_id=?,updated_at=?
                WHERE user_id=?
                """,
                (target_owner_id, _iso(now), source_owner_id),
            )
            counts["extraction_jobs"] = jobs_cursor.rowcount
            await self._audit_db(
                db,
                actor=f"identity-merge:{target_owner_id}",
                action="owner_merge",
                now=now,
            )
            await db.commit()
        return counts

    @staticmethod
    async def _classify_merge_memory(db, row, target_owner_id: str) -> str:
        duplicate = await (
            await db.execute(
                """
                SELECT id FROM memories
                WHERE scope_type=? AND owner_id=? AND space_id=?
                  AND COALESCE(domain_id,'')=COALESCE(?, '')
                  AND normalized_fact=? AND status='confirmed'
                LIMIT 1
                """,
                (
                    row["scope_type"],
                    target_owner_id,
                    row["space_id"],
                    row["domain_id"],
                    row["normalized_fact"],
                ),
            )
        ).fetchone()
        if duplicate is not None:
            return "duplicate"
        conflict = await (
            await db.execute(
                """
                SELECT id FROM memories
                WHERE scope_type=? AND owner_id=? AND space_id=?
                  AND COALESCE(domain_id,'')=COALESCE(?, '')
                  AND memory_type=? AND subject=? AND status='confirmed'
                LIMIT 1
                """,
                (
                    row["scope_type"],
                    target_owner_id,
                    row["space_id"],
                    row["domain_id"],
                    row["memory_type"],
                    row["subject"],
                ),
            )
        ).fetchone()
        return "conflict" if conflict is not None else "unique"

    async def search_memories(
        self, query: str, *, scope_type: str, owner_id: str, space_id: str = "middle-platform",
        domain_id: str | None = None, limit: int = 5,
    ) -> list[Memory]:
        terms = [item.strip() for item in query.replace("？", " ").replace("?", " ").split() if item.strip()]
        like = "%" + "%".join(terms or [query.strip()]) + "%"
        memories = await self.list_memories(
            scope_type=scope_type, owner_id=owner_id, space_id=space_id,
            domain_id=domain_id, limit=max(20, limit * 4),
        )
        return [
            item for item in memories
            if like.strip("%").casefold() in f"{item.subject} {item.normalized_fact} {item.summary}".casefold()
        ][: max(1, min(limit, 20))]

    async def expire_memories(self) -> int:
        now = _now()
        async with self._connect() as db:
            cursor = await db.execute(
                "UPDATE memories SET status='expired', updated_at=? WHERE status='confirmed' AND valid_until IS NOT NULL AND valid_until<=?",
                (_iso(now), _iso(now)),
            )
            await db.execute(
                "UPDATE memory_candidates SET status='expired', updated_at=? WHERE status='candidate' AND expires_at IS NOT NULL AND expires_at<=?",
                (_iso(now), _iso(now)),
            )
            await db.commit()
            return cursor.rowcount

    async def soft_delete_memory(self, memory_id: str, *, actor: str = "user") -> bool:
        now = _now()
        async with self._connect() as db:
            cursor = await db.execute(
                "UPDATE memories SET status='deleted', updated_at=? WHERE id=? AND status!='deleted'",
                (_iso(now), memory_id),
            )
            if cursor.rowcount:
                await self._audit_db(
                    db, memory_id=memory_id, actor=actor, action="delete", now=now
                )
            await db.commit()
            return cursor.rowcount == 1

    async def _audit_db(self, db, *, memory_id=None, candidate_id=None, actor, action, now):
        await db.execute(
            "INSERT INTO memory_audit_events(id,memory_id,candidate_id,actor,action,details_json,created_at) VALUES(?,?,?,?,?,?,?)",
            (str(uuid4()), memory_id, candidate_id, actor, action, "{}", _iso(now)),
        )

    @staticmethod
    def _candidate(row) -> MemoryCandidate:
        return MemoryCandidate(
            id=row["id"], scope_type=row["scope_type"], owner_id=row["owner_id"],
            space_id=row["space_id"], domain_id=row["domain_id"],
            memory_type=row["memory_type"], subject=row["subject"],
            normalized_fact=row["normalized_fact"], summary=row["summary"],
            source_turn_id=row["source_turn_id"],
            source_citations=tuple(json.loads(row["source_citations_json"] or "[]")),
            confidence=row["confidence"], status=row["status"],
            expires_at=_parse(row["expires_at"]), created_at=_parse(row["created_at"]),
            updated_at=_parse(row["updated_at"]),
        )

    @staticmethod
    def _memory(row) -> Memory:
        return Memory(
            id=row["id"], scope_type=row["scope_type"], owner_id=row["owner_id"],
            space_id=row["space_id"], domain_id=row["domain_id"],
            memory_type=row["memory_type"], subject=row["subject"],
            normalized_fact=row["normalized_fact"], summary=row["summary"],
            source_turn_id=row["source_turn_id"],
            source_citations=tuple(json.loads(row["source_citations_json"] or "[]")),
            confidence=row["confidence"], status=row["status"],
            valid_from=_parse(row["valid_from"]), valid_until=_parse(row["valid_until"]),
            last_used_at=_parse(row["last_used_at"]), supersedes_id=row["supersedes_id"],
            created_at=_parse(row["created_at"]), updated_at=_parse(row["updated_at"]),
        )

    @staticmethod
    def _extraction_job(row) -> MemoryExtractionJob:
        return MemoryExtractionJob(
            id=row["id"], user_id=row["user_id"],
            conversation_id=row["conversation_id"], space_id=row["space_id"],
            domain_id=row["domain_id"], channel=row["channel"],
            question=row["question"], answer=row["answer"],
            source_turn_id=row["source_turn_id"],
            source_citations=tuple(json.loads(row["source_citations_json"] or "[]")),
            status=row["status"], attempt=int(row["attempt"]),
            worker_id=row["worker_id"], error_type=row["error_type"],
            created_at=_parse(row["created_at"]), updated_at=_parse(row["updated_at"]),
        )


class PostgresMemoryRepository(MemoryRepository):
    def __init__(self, database_resources: DatabaseResources):
        self.database_resources = database_resources

    @asynccontextmanager
    async def _connect(self):
        async with PostgresCompatConnection(self.database_resources) as connection:
            yield connection

    async def initialize(self) -> None:
        if not await self.database_resources.check_ready():
            raise RuntimeError("PostgreSQL memory repository is unavailable")

    async def claim_extraction_job(self, worker_id: str) -> MemoryExtractionJob | None:
        now = _iso(_now())
        async with self._connect() as db:
            cursor = await db.execute(
                """
                WITH candidate AS (
                    SELECT id FROM memory_extraction_jobs
                    WHERE status='queued'
                    ORDER BY created_at, id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE memory_extraction_jobs AS job
                SET status='running', attempt=job.attempt+1,
                    worker_id=?, updated_at=?
                FROM candidate
                WHERE job.id=candidate.id
                RETURNING job.*
                """,
                (worker_id, now),
            )
            row = await cursor.fetchone()
            await db.commit()
        return self._extraction_job(row) if row is not None else None
