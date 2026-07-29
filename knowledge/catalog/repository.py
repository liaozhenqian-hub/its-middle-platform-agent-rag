from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, AsyncIterator, Iterable
from uuid import uuid4

import aiosqlite

from knowledge.persistence.database import DatabaseResources
from knowledge.persistence.sqlite_compat import PostgresCompatConnection

from knowledge.catalog.migrations import MIGRATIONS
from knowledge.catalog.models import (
    AuditEvent,
    ChunkCatalogCreate,
    ChunkCatalogEntry,
    CodeSymbol,
    CodeSymbolCreate,
    KnowledgeDomain,
    KnowledgeSource,
    KnowledgeSourceCreate,
    KnowledgeSpace,
    SourceDomainRule,
    SourceDomainRuleCreate,
    SourceFile,
    SourceFileCreate,
    SourceType,
    SourceVersion,
    SourceVersionCreate,
    SyncJob,
    SyncJobState,
)


class CatalogNotFoundError(LookupError):
    pass


class CatalogConflictError(RuntimeError):
    pass


@dataclass(frozen=True)
class _StoredAdminSession:
    id: str
    token_hash: str
    username: str
    csrf_token: str
    expires_at: datetime
    created_at: datetime


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _to_iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _from_iso(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _json_dump(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_load(value: str) -> dict[str, Any]:
    loaded = json.loads(value)
    if not isinstance(loaded, dict):
        raise ValueError("catalog JSON values must be objects")
    return loaded


class CatalogRepository:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[aiosqlite.Connection]:
        db = await aiosqlite.connect(self.db_path)
        db.row_factory = aiosqlite.Row
        try:
            await db.execute("PRAGMA foreign_keys = ON")
            await db.execute("PRAGMA busy_timeout = 5000")
            yield db
        finally:
            await db.close()

    async def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with self._connect() as db:
            await db.execute("PRAGMA journal_mode = WAL")
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
                """
            )
            await db.commit()

            for migration in MIGRATIONS:
                await db.execute("BEGIN IMMEDIATE")
                cursor = await db.execute(
                    "SELECT 1 FROM schema_migrations WHERE version=?",
                    (migration.version,),
                )
                if await cursor.fetchone() is not None:
                    await db.commit()
                    continue
                try:
                    for statement in migration.statements:
                        await db.execute(statement)
                    await db.execute(
                        """
                        INSERT INTO schema_migrations(version, name, applied_at)
                        VALUES (?, ?, ?)
                        """,
                        (migration.version, migration.name, _to_iso(_utc_now())),
                    )
                    await db.commit()
                except Exception:
                    await db.rollback()
                    raise

    async def check_ready(self) -> bool:
        try:
            async with self._connect() as db:
                cursor = await db.execute("SELECT COUNT(*) FROM schema_migrations")
                row = await cursor.fetchone()
            return row is not None and row[0] == len(MIGRATIONS)
        except Exception:
            return False

    async def list_spaces(self) -> list[KnowledgeSpace]:
        async with self._connect() as db:
            rows = await (
                await db.execute(
                    "SELECT id, name, created_at FROM knowledge_spaces ORDER BY name"
                )
            ).fetchall()
        return [
            KnowledgeSpace(id=row["id"], name=row["name"], created_at=_from_iso(row["created_at"]))
            for row in rows
        ]

    async def list_domains(self, space_id: str) -> list[KnowledgeDomain]:
        async with self._connect() as db:
            rows = await (
                await db.execute(
                    """
                    SELECT id, space_id, name, sort_order, created_at
                    FROM knowledge_domains
                    WHERE space_id=?
                    ORDER BY sort_order, id
                    """,
                    (space_id,),
                )
            ).fetchall()
        return [self._domain_from_row(row) for row in rows]

    async def create_source(self, source: KnowledgeSourceCreate) -> KnowledgeSource:
        if source.source_type is SourceType.GIT and source.domain_id is not None:
            raise ValueError("Git sources belong to a knowledge space, not one domain")
        if source.source_type is not SourceType.GIT and source.domain_id is None:
            raise ValueError("document and Swagger sources must belong to a domain")
        now = _to_iso(_utc_now())
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO knowledge_sources(
                    id, space_id, domain_id, source_type, name, config_json,
                    enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source.id,
                    source.space_id,
                    source.domain_id,
                    source.source_type.value,
                    source.name,
                    _json_dump(source.config),
                    bool(source.enabled),
                    now,
                    now,
                ),
            )
            await db.commit()
        created = await self.get_source(source.id)
        assert created is not None
        return created

    async def get_source(self, source_id: str) -> KnowledgeSource | None:
        async with self._connect() as db:
            row = await (
                await db.execute(
                    self._source_select() + " WHERE s.id=?",
                    (source_id,),
                )
            ).fetchone()
        return self._source_from_row(row) if row is not None else None

    async def list_sources(
        self,
        *,
        space_id: str | None = None,
        domain_id: str | None = None,
        source_type: SourceType | None = None,
        enabled: bool | None = None,
    ) -> list[KnowledgeSource]:
        clauses: list[str] = []
        values: list[Any] = []
        if space_id is not None:
            clauses.append("s.space_id=?")
            values.append(space_id)
        if domain_id is not None:
            clauses.append("s.domain_id=?")
            values.append(domain_id)
        if source_type is not None:
            clauses.append("s.source_type=?")
            values.append(source_type.value)
        if enabled is not None:
            clauses.append("s.enabled=?")
            values.append(bool(enabled))
        sql = self._source_select()
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY s.created_at, s.id"
        async with self._connect() as db:
            rows = await (await db.execute(sql, values)).fetchall()
        return [self._source_from_row(row) for row in rows]

    async def update_source(
        self,
        source_id: str,
        *,
        name: str | None = None,
        config: dict[str, Any] | None = None,
        enabled: bool | None = None,
    ) -> KnowledgeSource:
        assignments = ["updated_at=?"]
        values: list[Any] = [_to_iso(_utc_now())]
        if name is not None:
            assignments.append("name=?")
            values.append(name)
        if config is not None:
            assignments.append("config_json=?")
            values.append(_json_dump(config))
        if enabled is not None:
            assignments.append("enabled=?")
            values.append(bool(enabled))
        values.append(source_id)
        async with self._connect() as db:
            cursor = await db.execute(
                f"UPDATE knowledge_sources SET {', '.join(assignments)} WHERE id=?",
                values,
            )
            if cursor.rowcount != 1:
                await db.rollback()
                raise CatalogNotFoundError(source_id)
            await db.commit()
        updated = await self.get_source(source_id)
        assert updated is not None
        return updated

    async def delete_source(self, source_id: str) -> bool:
        async with self._connect() as db:
            cursor = await db.execute(
                "DELETE FROM knowledge_sources WHERE id=?", (source_id,)
            )
            await db.commit()
            return cursor.rowcount == 1

    async def purge_source_content_records(self, source_id: str) -> None:
        """Remove indexed-source metadata while retaining the soft-deleted source."""
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                source = await (
                    await db.execute(
                        "SELECT 1 FROM knowledge_sources WHERE id=?", (source_id,)
                    )
                ).fetchone()
                if source is None:
                    raise CatalogNotFoundError(source_id)
                for table in (
                    "source_domain_rules",
                    "swagger_cache",
                    "source_webhook_secrets",
                    "encrypted_secrets",
                    "source_versions",
                ):
                    await db.execute(
                        f"DELETE FROM {table} WHERE source_id=?", (source_id,)
                    )
                await db.commit()
            except Exception:
                if db.in_transaction:
                    await db.rollback()
                raise

    async def create_domain_rule(
        self, rule: SourceDomainRuleCreate
    ) -> SourceDomainRule:
        now = _to_iso(_utc_now())
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO source_domain_rules(
                    id, source_id, pattern, target_domain_id, shared, priority,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rule.id,
                    rule.source_id,
                    rule.pattern,
                    rule.target_domain_id,
                    bool(rule.shared),
                    rule.priority,
                    now,
                ),
            )
            await db.commit()
        return SourceDomainRule(
            id=rule.id,
            source_id=rule.source_id,
            pattern=rule.pattern,
            target_domain_id=rule.target_domain_id,
            shared=rule.shared,
            priority=rule.priority,
            created_at=_from_iso(now),
        )

    async def list_domain_rules(self, source_id: str) -> list[SourceDomainRule]:
        async with self._connect() as db:
            rows = await (
                await db.execute(
                    """
                    SELECT id, source_id, pattern, target_domain_id, shared,
                           priority, created_at
                    FROM source_domain_rules WHERE source_id=?
                    ORDER BY priority, id
                    """,
                    (source_id,),
                )
            ).fetchall()
        return [self._domain_rule_from_row(row) for row in rows]

    async def delete_domain_rule(self, rule_id: str) -> bool:
        async with self._connect() as db:
            cursor = await db.execute(
                "DELETE FROM source_domain_rules WHERE id=?", (rule_id,)
            )
            await db.commit()
            return cursor.rowcount == 1

    async def replace_domain_rules(
        self,
        source_id: str,
        rules: list[SourceDomainRuleCreate],
    ) -> list[SourceDomainRule]:
        if any(rule.source_id != source_id for rule in rules):
            raise ValueError("all domain rules must belong to the requested source")
        now = _to_iso(_utc_now())
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                source = await (
                    await db.execute(
                        "SELECT source_type FROM knowledge_sources WHERE id=?",
                        (source_id,),
                    )
                ).fetchone()
                if source is None:
                    raise CatalogNotFoundError(source_id)
                if source["source_type"] != SourceType.GIT.value:
                    raise CatalogConflictError("only Git sources have domain rules")
                await db.execute(
                    "DELETE FROM source_domain_rules WHERE source_id=?", (source_id,)
                )
                for rule in rules:
                    await db.execute(
                        """
                        INSERT INTO source_domain_rules(
                            id, source_id, pattern, target_domain_id,
                            shared, priority, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            rule.id,
                            source_id,
                            rule.pattern,
                            rule.target_domain_id,
                            bool(rule.shared),
                            rule.priority,
                            now,
                        ),
                    )
                await db.commit()
            except Exception:
                if db.in_transaction:
                    await db.rollback()
                raise
        return await self.list_domain_rules(source_id)

    async def create_version(self, version: SourceVersionCreate) -> SourceVersion:
        now = _to_iso(_utc_now())
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                if version.current:
                    await db.execute(
                        "UPDATE source_versions SET current=0, updated_at=? WHERE source_id=?",
                        (now, version.source_id),
                    )
                await db.execute(
                    """
                    INSERT INTO source_versions(
                        id, source_id, version_ref, status, current,
                        metadata_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        version.id,
                        version.source_id,
                        version.version_ref,
                        version.status,
                        bool(version.current),
                        _json_dump(version.metadata),
                        now,
                        now,
                    ),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return (await self.list_versions(version.source_id, version_id=version.id))[0]

    async def list_versions(
        self, source_id: str, *, version_id: str | None = None
    ) -> list[SourceVersion]:
        sql = """
            SELECT id, source_id, version_ref, status, current, metadata_json,
                   created_at, updated_at
            FROM source_versions WHERE source_id=?
        """
        values: list[Any] = [source_id]
        if version_id is not None:
            sql += " AND id=?"
            values.append(version_id)
        sql += " ORDER BY current DESC, created_at DESC, id"
        async with self._connect() as db:
            rows = await (await db.execute(sql, values)).fetchall()
        return [self._version_from_row(row) for row in rows]

    async def update_version(
        self,
        version_id: str,
        *,
        status: str | None = None,
        current: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SourceVersion:
        now = _to_iso(_utc_now())
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                existing = await (
                    await db.execute(
                        "SELECT source_id FROM source_versions WHERE id=?",
                        (version_id,),
                    )
                ).fetchone()
                if existing is None:
                    await db.rollback()
                    raise CatalogNotFoundError(version_id)
                source_id = existing["source_id"]
                if current is True:
                    await db.execute(
                        """
                        UPDATE source_versions
                        SET current=0, updated_at=?
                        WHERE source_id=? AND id<>? AND current=1
                        """,
                        (now, source_id, version_id),
                    )
                assignments = ["updated_at=?"]
                values: list[Any] = [now]
                if status is not None:
                    assignments.append("status=?")
                    values.append(status)
                if current is not None:
                    assignments.append("current=?")
                    values.append(bool(current))
                if metadata is not None:
                    assignments.append("metadata_json=?")
                    values.append(_json_dump(metadata))
                values.append(version_id)
                await db.execute(
                    f"UPDATE source_versions SET {', '.join(assignments)} WHERE id=?",
                    values,
                )
                row = await (
                    await db.execute(
                        """
                        SELECT id, source_id, version_ref, status, current,
                               metadata_json, created_at, updated_at
                        FROM source_versions WHERE id=?
                        """,
                        (version_id,),
                    )
                ).fetchone()
                await db.commit()
            except Exception:
                if db.in_transaction:
                    await db.rollback()
                raise
        return self._version_from_row(row)

    async def delete_version(self, version_id: str) -> bool:
        async with self._connect() as db:
            cursor = await db.execute(
                "DELETE FROM source_versions WHERE id=?", (version_id,)
            )
            await db.commit()
            return cursor.rowcount == 1

    async def create_file(self, source_file: SourceFileCreate) -> SourceFile:
        now = _to_iso(_utc_now())
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO source_files(
                    id, source_id, version_id, relative_path, domain_key,
                    language, content_hash, size_bytes, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_file.id,
                    source_file.source_id,
                    source_file.version_id,
                    source_file.relative_path,
                    source_file.domain_key,
                    source_file.language,
                    source_file.content_hash,
                    source_file.size_bytes,
                    _json_dump(source_file.metadata),
                    now,
                ),
            )
            await db.commit()
        rows = await self.list_files(
            source_file.source_id, source_file.version_id, file_id=source_file.id
        )
        return rows[0]

    async def list_files(
        self,
        source_id: str,
        version_id: str | None = None,
        *,
        file_id: str | None = None,
    ) -> list[SourceFile]:
        clauses = ["source_id=?"]
        values: list[Any] = [source_id]
        if version_id is not None:
            clauses.append("version_id=?")
            values.append(version_id)
        if file_id is not None:
            clauses.append("id=?")
            values.append(file_id)
        sql = f"""
            SELECT id, source_id, version_id, relative_path, domain_key,
                   language, content_hash, size_bytes, metadata_json, created_at
            FROM source_files WHERE {' AND '.join(clauses)}
            ORDER BY relative_path, id
        """
        async with self._connect() as db:
            rows = await (await db.execute(sql, values)).fetchall()
        return [self._file_from_row(row) for row in rows]

    async def create_symbol(self, symbol: CodeSymbolCreate) -> CodeSymbol:
        if symbol.start_line <= 0 or symbol.end_line < symbol.start_line:
            raise ValueError("symbol line range is invalid")
        now = _to_iso(_utc_now())
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO code_symbols(
                    id, source_file_id, symbol_type, name, qualified_name,
                    start_line, end_line, parent_symbol_id, metadata_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol.id,
                    symbol.source_file_id,
                    symbol.symbol_type,
                    symbol.name,
                    symbol.qualified_name,
                    symbol.start_line,
                    symbol.end_line,
                    symbol.parent_symbol_id,
                    _json_dump(symbol.metadata),
                    now,
                ),
            )
            await db.commit()
        return (await self.list_symbols(symbol.source_file_id, symbol_id=symbol.id))[0]

    async def list_symbols(
        self, source_file_id: str, *, symbol_id: str | None = None
    ) -> list[CodeSymbol]:
        sql = """
            SELECT id, source_file_id, symbol_type, name, qualified_name,
                   start_line, end_line, parent_symbol_id, metadata_json,
                   created_at
            FROM code_symbols WHERE source_file_id=?
        """
        values: list[Any] = [source_file_id]
        if symbol_id is not None:
            sql += " AND id=?"
            values.append(symbol_id)
        sql += " ORDER BY start_line, end_line, id"
        async with self._connect() as db:
            rows = await (await db.execute(sql, values)).fetchall()
        return [self._symbol_from_row(row) for row in rows]

    async def upsert_chunk(self, chunk: ChunkCatalogCreate) -> ChunkCatalogEntry:
        now = _to_iso(_utc_now())
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO chunk_catalog(
                    chunk_id, source_id, version_id, source_file_id, source_type,
                    domain_key, locator, content_hash, metadata_json, created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chunk_id) DO UPDATE SET
                    source_id=excluded.source_id,
                    version_id=excluded.version_id,
                    source_file_id=excluded.source_file_id,
                    source_type=excluded.source_type,
                    domain_key=excluded.domain_key,
                    locator=excluded.locator,
                    content_hash=excluded.content_hash,
                    metadata_json=excluded.metadata_json,
                    updated_at=excluded.updated_at
                """,
                (
                    chunk.chunk_id,
                    chunk.source_id,
                    chunk.version_id,
                    chunk.source_file_id,
                    chunk.source_type.value,
                    chunk.domain_key,
                    chunk.locator,
                    chunk.content_hash,
                    _json_dump(chunk.metadata),
                    now,
                    now,
                ),
            )
            await db.commit()
        return (await self.list_chunks(chunk_ids=[chunk.chunk_id]))[0]

    async def list_chunks(
        self,
        *,
        source_id: str | None = None,
        version_id: str | None = None,
        domain_key: str | None = None,
        source_type: SourceType | None = None,
        chunk_ids: Iterable[str] | None = None,
    ) -> list[ChunkCatalogEntry]:
        clauses: list[str] = []
        values: list[Any] = []
        if source_id is not None:
            clauses.append("source_id=?")
            values.append(source_id)
        if version_id is not None:
            clauses.append("version_id=?")
            values.append(version_id)
        if domain_key is not None:
            clauses.append("domain_key=?")
            values.append(domain_key)
        if source_type is not None:
            clauses.append("source_type=?")
            values.append(source_type.value)
        if chunk_ids is not None:
            ids = list(chunk_ids)
            if not ids:
                return []
            clauses.append(f"chunk_id IN ({','.join('?' for _ in ids)})")
            values.extend(ids)
        sql = """
            SELECT chunk_id, source_id, version_id, source_file_id, source_type,
                   domain_key, locator, content_hash, metadata_json, created_at,
                   updated_at
            FROM chunk_catalog
        """
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY chunk_id"
        async with self._connect() as db:
            rows = await (await db.execute(sql, values)).fetchall()
        return [self._chunk_from_row(row) for row in rows]

    async def delete_chunks(self, chunk_ids: Iterable[str]) -> int:
        ids = list(chunk_ids)
        if not ids:
            return 0
        async with self._connect() as db:
            cursor = await db.execute(
                f"DELETE FROM chunk_catalog WHERE chunk_id IN ({','.join('?' for _ in ids)})",
                ids,
            )
            await db.commit()
            return cursor.rowcount

    async def delete_chunks_for_source(self, source_id: str) -> int:
        async with self._connect() as db:
            cursor = await db.execute(
                "DELETE FROM chunk_catalog WHERE source_id=?", (source_id,)
            )
            await db.commit()
            return cursor.rowcount

    async def enqueue_job(
        self,
        *,
        source_id: str,
        kind: str,
        target_commit: str | None = None,
        available_at: datetime | None = None,
        now: datetime | None = None,
    ) -> SyncJob:
        kind = kind.strip()
        if not kind:
            raise ValueError("job kind must not be empty")
        if target_commit is not None:
            target_commit = target_commit.strip() or None
        if kind == "webhook" and target_commit is None:
            raise ValueError("webhook jobs require a nonempty target_commit")
        now = now or _utc_now()
        available_at = available_at or now
        job_id = str(uuid4())
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                if target_commit is not None:
                    existing = await (
                        await db.execute(
                            """
                            SELECT * FROM sync_jobs
                            WHERE source_id=? AND kind=? AND target_commit=?
                            """,
                            (source_id, kind, target_commit),
                        )
                    ).fetchone()
                    if existing is not None:
                        await db.commit()
                        return self._job_from_row(existing)
                timestamp = _to_iso(now)
                await db.execute(
                    """
                    INSERT INTO sync_jobs(
                        id, source_id, kind, state, target_commit, attempt, error,
                        worker_id, available_at, claimed_at, finished_at,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, 'queued', ?, 0, NULL, NULL, ?, NULL,
                              NULL, ?, ?)
                    """,
                    (
                        job_id,
                        source_id,
                        kind,
                        target_commit,
                        _to_iso(available_at),
                        timestamp,
                        timestamp,
                    ),
                )
                row = await (
                    await db.execute("SELECT * FROM sync_jobs WHERE id=?", (job_id,))
                ).fetchone()
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return self._job_from_row(row)

    async def claim_next_job(
        self, worker_id: str, *, now: datetime | None = None
    ) -> SyncJob | None:
        worker_id = worker_id.strip()
        if not worker_id:
            raise ValueError("worker_id must not be blank")
        now = now or _utc_now()
        now_iso = _to_iso(now)
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                row = await (
                    await db.execute(
                        """
                        SELECT id FROM sync_jobs
                        WHERE state='queued' AND available_at<=?
                        ORDER BY available_at, created_at, id
                        LIMIT 1
                        """,
                        (now_iso,),
                    )
                ).fetchone()
                if row is None:
                    await db.commit()
                    return None
                job_id = row["id"]
                await db.execute(
                    """
                    UPDATE sync_jobs
                    SET state='running', attempt=attempt+1, worker_id=?,
                        claimed_at=?, finished_at=NULL, updated_at=?
                    WHERE id=? AND state='queued'
                    """,
                    (worker_id, now_iso, now_iso, job_id),
                )
                claimed = await (
                    await db.execute("SELECT * FROM sync_jobs WHERE id=?", (job_id,))
                ).fetchone()
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return self._job_from_row(claimed)

    async def get_job(self, job_id: str) -> SyncJob:
        async with self._connect() as db:
            row = await (
                await db.execute("SELECT * FROM sync_jobs WHERE id=?", (job_id,))
            ).fetchone()
        if row is None:
            raise CatalogNotFoundError(job_id)
        return self._job_from_row(row)

    async def delete_queued_job(self, job_id: str) -> bool:
        """Cancel a job that has not yet been leased by a worker."""
        async with self._connect() as db:
            cursor = await db.execute(
                "DELETE FROM sync_jobs WHERE id=? AND state='queued'", (job_id,)
            )
            await db.commit()
            return cursor.rowcount == 1

    async def list_jobs(
        self,
        *,
        source_id: str | None = None,
        state: SyncJobState | None = None,
        limit: int = 100,
    ) -> list[SyncJob]:
        clauses: list[str] = []
        values: list[Any] = []
        if source_id is not None:
            clauses.append("source_id=?")
            values.append(source_id)
        if state is not None:
            clauses.append("state=?")
            values.append(state.value)
        sql = "SELECT * FROM sync_jobs"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        values.append(max(1, limit))
        async with self._connect() as db:
            rows = await (await db.execute(sql, values)).fetchall()
        return [self._job_from_row(row) for row in rows]

    async def complete_job(
        self,
        job_id: str,
        *,
        worker_id: str,
        attempt: int,
        now: datetime | None = None,
    ) -> SyncJob:
        return await self._finish_job(
            job_id,
            SyncJobState.SUCCEEDED,
            worker_id=worker_id,
            attempt=attempt,
            error=None,
            now=now,
        )

    async def fail_job(
        self,
        job_id: str,
        error: str,
        *,
        worker_id: str,
        attempt: int,
        now: datetime | None = None,
    ) -> SyncJob:
        return await self._finish_job(
            job_id,
            SyncJobState.FAILED,
            worker_id=worker_id,
            attempt=attempt,
            error=error,
            now=now,
        )

    async def _finish_job(
        self,
        job_id: str,
        state: SyncJobState,
        *,
        worker_id: str,
        attempt: int,
        error: str | None,
        now: datetime | None,
    ) -> SyncJob:
        if not worker_id.strip() or attempt <= 0:
            raise ValueError("worker_id and a positive attempt are required")
        timestamp = _to_iso(now or _utc_now())
        async with self._connect() as db:
            cursor = await db.execute(
                """
                UPDATE sync_jobs
                SET state=?, error=?, finished_at=?, updated_at=?
                WHERE id=? AND state='running' AND worker_id=? AND attempt=?
                """,
                (
                    state.value,
                    error,
                    timestamp,
                    timestamp,
                    job_id,
                    worker_id,
                    attempt,
                ),
            )
            if cursor.rowcount != 1:
                exists = await (
                    await db.execute("SELECT 1 FROM sync_jobs WHERE id=?", (job_id,))
                ).fetchone()
                await db.rollback()
                if exists is None:
                    raise CatalogNotFoundError(job_id)
                raise CatalogConflictError(f"job {job_id} is not running")
            await db.commit()
        return await self.get_job(job_id)

    async def requeue_job(
        self,
        job_id: str,
        *,
        available_at: datetime,
        worker_id: str | None = None,
        attempt: int | None = None,
        now: datetime | None = None,
    ) -> SyncJob:
        timestamp = _to_iso(now or _utc_now())
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                existing = await (
                    await db.execute(
                        "SELECT state FROM sync_jobs WHERE id=?", (job_id,)
                    )
                ).fetchone()
                if existing is None:
                    await db.rollback()
                    raise CatalogNotFoundError(job_id)
                if existing["state"] == SyncJobState.FAILED.value:
                    lease_supplied = worker_id is not None or attempt is not None
                    if not lease_supplied:
                        condition = "id=? AND state='failed'"
                        condition_values: tuple[Any, ...] = (job_id,)
                    else:
                        if (
                            worker_id is None
                            or not worker_id.strip()
                            or attempt is None
                            or attempt <= 0
                        ):
                            await db.rollback()
                            raise CatalogConflictError(
                                "worker retries require both worker_id and attempt"
                            )
                        condition = (
                            "id=? AND state='failed' AND worker_id=? AND attempt=?"
                        )
                        condition_values = (job_id, worker_id, attempt)
                elif existing["state"] == SyncJobState.RUNNING.value:
                    if (
                        worker_id is None
                        or not worker_id.strip()
                        or attempt is None
                        or attempt <= 0
                    ):
                        await db.rollback()
                        raise CatalogConflictError(
                            "running jobs require their current worker_id and attempt"
                        )
                    condition = (
                        "id=? AND state='running' AND worker_id=? AND attempt=?"
                    )
                    condition_values = (job_id, worker_id, attempt)
                else:
                    await db.rollback()
                    raise CatalogConflictError(f"job {job_id} cannot be requeued")
                cursor = await db.execute(
                    f"""
                    UPDATE sync_jobs
                    SET state='queued', error=NULL, worker_id=NULL,
                        claimed_at=NULL, finished_at=NULL, available_at=?,
                        updated_at=?
                    WHERE {condition}
                    """,
                    (_to_iso(available_at), timestamp, *condition_values),
                )
                if cursor.rowcount != 1:
                    await db.rollback()
                    raise CatalogConflictError(
                        f"job {job_id} ownership changed before it was requeued"
                    )
                await db.commit()
            except Exception:
                if db.in_transaction:
                    await db.rollback()
                raise
        return await self.get_job(job_id)

    async def requeue_stale_jobs(
        self,
        *,
        stale_before: datetime,
        now: datetime | None = None,
    ) -> int:
        timestamp = _to_iso(now or _utc_now())
        async with self._connect() as db:
            cursor = await db.execute(
                """
                UPDATE sync_jobs
                SET state='queued', worker_id=NULL, claimed_at=NULL,
                    finished_at=NULL, available_at=?, updated_at=?
                WHERE state='running' AND claimed_at<=?
                """,
                (timestamp, timestamp, _to_iso(stale_before)),
            )
            await db.commit()
            return cursor.rowcount

    async def append_audit_event(
        self,
        *,
        actor: str,
        action: str,
        resource_type: str,
        resource_id: str | None,
        details: dict[str, Any],
        now: datetime | None = None,
    ) -> AuditEvent:
        event_id = str(uuid4())
        timestamp = _to_iso(now or _utc_now())
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO audit_events(
                    id, actor, action, resource_type, resource_id, details_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    actor,
                    action,
                    resource_type,
                    resource_id,
                    _json_dump(details),
                    timestamp,
                ),
            )
            await db.commit()
        return AuditEvent(
            id=event_id,
            actor=actor,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
            created_at=_from_iso(timestamp),
        )

    async def list_audit_events(
        self,
        *,
        actor: str | None = None,
        resource_type: str | None = None,
        limit: int = 100,
    ) -> list[AuditEvent]:
        clauses: list[str] = []
        values: list[Any] = []
        if actor is not None:
            clauses.append("actor=?")
            values.append(actor)
        if resource_type is not None:
            clauses.append("resource_type=?")
            values.append(resource_type)
        sql = "SELECT * FROM audit_events"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        values.append(max(1, limit))
        async with self._connect() as db:
            rows = await (await db.execute(sql, values)).fetchall()
        return [self._audit_from_row(row) for row in rows]

    async def _set_encrypted_secret(
        self, source_id: str, secret_kind: str, encrypted_value: str
    ) -> None:
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO encrypted_secrets(
                    source_id, secret_kind, encrypted_value, updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(source_id, secret_kind) DO UPDATE SET
                    encrypted_value=excluded.encrypted_value,
                    updated_at=excluded.updated_at
                """,
                (source_id, secret_kind, encrypted_value, _to_iso(_utc_now())),
            )
            await db.commit()

    async def put_swagger_cache(
        self,
        source_id: str,
        *,
        specification: dict[str, Any],
        etag: str | None,
        last_modified: str | None,
        refreshed_at: datetime,
    ) -> None:
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO swagger_cache(
                    source_id, specification_json, etag, last_modified, refreshed_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    specification_json=excluded.specification_json,
                    etag=excluded.etag,
                    last_modified=excluded.last_modified,
                    refreshed_at=excluded.refreshed_at
                """,
                (
                    source_id,
                    _json_dump(specification),
                    etag,
                    last_modified,
                    _to_iso(refreshed_at),
                ),
            )
            await db.commit()

    async def get_swagger_cache(self, source_id: str) -> dict[str, Any] | None:
        async with self._connect() as db:
            row = await (
                await db.execute(
                    "SELECT * FROM swagger_cache WHERE source_id=?",
                    (source_id,),
                )
            ).fetchone()
        if row is None:
            return None
        return {
            "specification": _json_load(row["specification_json"]),
            "etag": row["etag"],
            "last_modified": row["last_modified"],
            "refreshed_at": _from_iso(row["refreshed_at"]),
        }

    async def set_webhook_secret_hash(
        self, source_id: str, secret_hash: str
    ) -> None:
        if not secret_hash:
            raise ValueError("secret_hash must not be empty")
        now = _to_iso(_utc_now())
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO source_webhook_secrets(source_id, secret_hash, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    secret_hash=excluded.secret_hash,
                    updated_at=excluded.updated_at
                """,
                (source_id, secret_hash, now),
            )
            await db.commit()

    async def get_webhook_secret_hash(self, source_id: str) -> str | None:
        async with self._connect() as db:
            row = await (
                await db.execute(
                    """
                    SELECT secret_hash FROM source_webhook_secrets
                    WHERE source_id=?
                    """,
                    (source_id,),
                )
            ).fetchone()
        return row[0] if row is not None else None

    async def _get_encrypted_secret(
        self, source_id: str, secret_kind: str
    ) -> str | None:
        async with self._connect() as db:
            row = await (
                await db.execute(
                    """
                    SELECT encrypted_value FROM encrypted_secrets
                    WHERE source_id=? AND secret_kind=?
                    """,
                    (source_id, secret_kind),
                )
            ).fetchone()
        return row[0] if row is not None else None

    async def _delete_encrypted_secret(
        self, source_id: str, secret_kind: str
    ) -> bool:
        async with self._connect() as db:
            cursor = await db.execute(
                """
                DELETE FROM encrypted_secrets
                WHERE source_id=? AND secret_kind=?
                """,
                (source_id, secret_kind),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def _delete_all_encrypted_secrets(self, source_id: str) -> int:
        async with self._connect() as db:
            cursor = await db.execute(
                "DELETE FROM encrypted_secrets WHERE source_id=?",
                (source_id,),
            )
            await db.commit()
            return cursor.rowcount

    async def _insert_admin_session(
        self,
        *,
        session_id: str,
        token_hash: str,
        username: str,
        csrf_token: str,
        expires_at: datetime,
        created_at: datetime,
    ) -> None:
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO admin_sessions(
                    id, token_hash, username, csrf_token, expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    token_hash,
                    username,
                    csrf_token,
                    _to_iso(expires_at),
                    _to_iso(created_at),
                ),
            )
            await db.commit()

    async def _get_admin_session(
        self, token_hash: str
    ) -> _StoredAdminSession | None:
        async with self._connect() as db:
            row = await (
                await db.execute(
                    "SELECT * FROM admin_sessions WHERE token_hash=?",
                    (token_hash,),
                )
            ).fetchone()
        if row is None:
            return None
        return _StoredAdminSession(
            id=row["id"],
            token_hash=row["token_hash"],
            username=row["username"],
            csrf_token=row["csrf_token"],
            expires_at=_from_iso(row["expires_at"]),
            created_at=_from_iso(row["created_at"]),
        )

    async def _delete_admin_session(self, token_hash: str) -> bool:
        async with self._connect() as db:
            cursor = await db.execute(
                "DELETE FROM admin_sessions WHERE token_hash=?", (token_hash,)
            )
            await db.commit()
            return cursor.rowcount == 1

    async def _cleanup_admin_sessions(self, expires_at: datetime) -> int:
        async with self._connect() as db:
            cursor = await db.execute(
                "DELETE FROM admin_sessions WHERE expires_at<=?",
                (_to_iso(expires_at),),
            )
            await db.commit()
            return cursor.rowcount

    @staticmethod
    def _source_select() -> str:
        return """
            SELECT s.id, s.space_id, s.domain_id, s.source_type, s.name,
                   s.config_json, s.enabled, s.created_at, s.updated_at,
                   EXISTS(
                       SELECT 1 FROM encrypted_secrets es WHERE es.source_id=s.id
                   ) AS credential_configured
            FROM knowledge_sources s
        """

    @staticmethod
    def _source_from_row(row: aiosqlite.Row) -> KnowledgeSource:
        return KnowledgeSource(
            id=row["id"],
            space_id=row["space_id"],
            domain_id=row["domain_id"],
            source_type=SourceType(row["source_type"]),
            name=row["name"],
            config=_json_load(row["config_json"]),
            enabled=bool(row["enabled"]),
            credential_configured=bool(row["credential_configured"]),
            created_at=_from_iso(row["created_at"]),
            updated_at=_from_iso(row["updated_at"]),
        )

    @staticmethod
    def _domain_from_row(row: aiosqlite.Row) -> KnowledgeDomain:
        return KnowledgeDomain(
            id=row["id"],
            space_id=row["space_id"],
            name=row["name"],
            sort_order=row["sort_order"],
            created_at=_from_iso(row["created_at"]),
        )

    @staticmethod
    def _domain_rule_from_row(row: aiosqlite.Row) -> SourceDomainRule:
        return SourceDomainRule(
            id=row["id"],
            source_id=row["source_id"],
            pattern=row["pattern"],
            target_domain_id=row["target_domain_id"],
            shared=bool(row["shared"]),
            priority=row["priority"],
            created_at=_from_iso(row["created_at"]),
        )

    @staticmethod
    def _version_from_row(row: aiosqlite.Row) -> SourceVersion:
        return SourceVersion(
            id=row["id"],
            source_id=row["source_id"],
            version_ref=row["version_ref"],
            status=row["status"],
            current=bool(row["current"]),
            metadata=_json_load(row["metadata_json"]),
            created_at=_from_iso(row["created_at"]),
            updated_at=_from_iso(row["updated_at"]),
        )

    @staticmethod
    def _file_from_row(row: aiosqlite.Row) -> SourceFile:
        return SourceFile(
            id=row["id"],
            source_id=row["source_id"],
            version_id=row["version_id"],
            relative_path=row["relative_path"],
            domain_key=row["domain_key"],
            language=row["language"],
            content_hash=row["content_hash"],
            size_bytes=row["size_bytes"],
            metadata=_json_load(row["metadata_json"]),
            created_at=_from_iso(row["created_at"]),
        )

    @staticmethod
    def _symbol_from_row(row: aiosqlite.Row) -> CodeSymbol:
        return CodeSymbol(
            id=row["id"],
            source_file_id=row["source_file_id"],
            symbol_type=row["symbol_type"],
            name=row["name"],
            qualified_name=row["qualified_name"],
            start_line=row["start_line"],
            end_line=row["end_line"],
            parent_symbol_id=row["parent_symbol_id"],
            metadata=_json_load(row["metadata_json"]),
            created_at=_from_iso(row["created_at"]),
        )

    @staticmethod
    def _chunk_from_row(row: aiosqlite.Row) -> ChunkCatalogEntry:
        return ChunkCatalogEntry(
            chunk_id=row["chunk_id"],
            source_id=row["source_id"],
            version_id=row["version_id"],
            source_file_id=row["source_file_id"],
            source_type=SourceType(row["source_type"]),
            domain_key=row["domain_key"],
            locator=row["locator"],
            content_hash=row["content_hash"],
            metadata=_json_load(row["metadata_json"]),
            created_at=_from_iso(row["created_at"]),
            updated_at=_from_iso(row["updated_at"]),
        )

    @staticmethod
    def _job_from_row(row: aiosqlite.Row) -> SyncJob:
        return SyncJob(
            id=row["id"],
            source_id=row["source_id"],
            kind=row["kind"],
            state=SyncJobState(row["state"]),
            target_commit=row["target_commit"],
            attempt=row["attempt"],
            error=row["error"],
            worker_id=row["worker_id"],
            available_at=_from_iso(row["available_at"]),
            claimed_at=_from_iso(row["claimed_at"]),
            finished_at=_from_iso(row["finished_at"]),
            created_at=_from_iso(row["created_at"]),
            updated_at=_from_iso(row["updated_at"]),
        )

    @staticmethod
    def _audit_from_row(row: aiosqlite.Row) -> AuditEvent:
        return AuditEvent(
            id=row["id"],
            actor=row["actor"],
            action=row["action"],
            resource_type=row["resource_type"],
            resource_id=row["resource_id"],
            details=_json_load(row["details_json"]),
            created_at=_from_iso(row["created_at"]),
        )


class PostgresCatalogRepository(CatalogRepository):
    def __init__(self, database_resources: DatabaseResources):
        self.database_resources = database_resources

    @asynccontextmanager
    async def _connect(self):
        async with PostgresCompatConnection(self.database_resources) as connection:
            yield connection

    async def initialize(self) -> None:
        if not await self.database_resources.check_ready():
            raise RuntimeError("PostgreSQL catalog repository is unavailable")

    async def check_ready(self) -> bool:
        return await self.database_resources.check_ready()

    async def claim_next_job(
        self, worker_id: str, *, now: datetime | None = None
    ) -> SyncJob | None:
        worker_id = worker_id.strip()
        if not worker_id:
            raise ValueError("worker_id must not be blank")
        current = _to_iso(now or _utc_now())
        async with self._connect() as db:
            cursor = await db.execute(
                """
                WITH candidate AS (
                    SELECT id FROM sync_jobs
                    WHERE state='queued' AND available_at<=?
                    ORDER BY available_at, created_at, id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE sync_jobs AS job
                SET state='running', attempt=job.attempt+1, worker_id=?,
                    claimed_at=?, finished_at=NULL, updated_at=?
                FROM candidate
                WHERE job.id=candidate.id
                RETURNING job.*
                """,
                (current, worker_id, current, current),
            )
            row = await cursor.fetchone()
            await db.commit()
        return self._job_from_row(row) if row is not None else None
