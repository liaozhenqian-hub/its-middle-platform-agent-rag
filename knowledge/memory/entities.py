from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import AsyncIterator, Iterable

import aiosqlite

from knowledge.persistence.database import DatabaseResources
from knowledge.persistence.sqlite_compat import PostgresCompatConnection


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _stable_id(prefix: str, *parts: str) -> str:
    payload = "\x1f".join(parts).encode("utf-8")
    return f"{prefix}-" + hashlib.sha256(payload).hexdigest()[:24]


@dataclass(frozen=True, slots=True)
class MemoryEntity:
    id: str
    scope_type: str
    owner_id: str
    space_id: str
    domain_id: str | None
    entity_type: str
    canonical_name: str
    branch: str | None
    environment: str | None
    aliases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EntityRelationMatch:
    relation_id: str
    relation_type: str
    source_name: str
    source_type: str
    target_name: str
    target_type: str
    summary: str
    branch: str | None
    environment: str | None
    confidence: float
    evidence_ids: tuple[str, ...]
    evidence_refs: tuple[tuple[str, str], ...]


class EntityMemoryRepository:
    _SCOPES = {"user", "domain", "global"}

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[aiosqlite.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        database = await aiosqlite.connect(self.db_path)
        database.row_factory = aiosqlite.Row
        await database.execute("PRAGMA busy_timeout=5000")
        try:
            yield database
        finally:
            await database.close()

    async def initialize(self) -> None:
        async with self._connect() as database:
            await database.execute("PRAGMA journal_mode=WAL")
            await database.execute("PRAGMA foreign_keys=ON")
            await database.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_entities (
                    id TEXT PRIMARY KEY,
                    scope_type TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    space_id TEXT NOT NULL,
                    domain_id TEXT,
                    entity_type TEXT NOT NULL,
                    canonical_name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL,
                    branch TEXT,
                    environment TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(scope_type,owner_id,space_id,entity_type,normalized_name,branch,environment)
                );
                CREATE INDEX IF NOT EXISTS idx_memory_entities_scope
                    ON memory_entities(scope_type,owner_id,space_id,domain_id,branch,environment,status);
                CREATE TABLE IF NOT EXISTS memory_entity_aliases (
                    entity_id TEXT NOT NULL,
                    alias TEXT NOT NULL,
                    normalized_alias TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(entity_id,normalized_alias),
                    FOREIGN KEY(entity_id) REFERENCES memory_entities(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_memory_entity_alias
                    ON memory_entity_aliases(normalized_alias);
                CREATE TABLE IF NOT EXISTS memory_entity_relations (
                    id TEXT PRIMARY KEY,
                    source_entity_id TEXT NOT NULL,
                    target_entity_id TEXT NOT NULL,
                    relation_type TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(source_entity_id,target_entity_id,relation_type),
                    FOREIGN KEY(source_entity_id) REFERENCES memory_entities(id) ON DELETE CASCADE,
                    FOREIGN KEY(target_entity_id) REFERENCES memory_entities(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS memory_entity_evidence (
                    relation_id TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(relation_id,source_type,source_id),
                    FOREIGN KEY(relation_id) REFERENCES memory_entity_relations(id) ON DELETE CASCADE
                );
                """
            )
            await database.commit()

    async def upsert_entity(
        self,
        *,
        scope_type: str,
        owner_id: str,
        space_id: str,
        domain_id: str | None,
        entity_type: str,
        canonical_name: str,
        branch: str | None = None,
        environment: str | None = None,
        aliases: Iterable[str] = (),
    ) -> MemoryEntity:
        if scope_type not in self._SCOPES:
            raise ValueError("invalid entity memory scope")
        normalized = self._normalize(canonical_name)
        if not owner_id.strip() or not space_id.strip() or not entity_type.strip() or not normalized:
            raise ValueError("entity namespace and name are required")
        entity_id = _stable_id(
            "entity", scope_type, owner_id, space_id, entity_type,
            normalized, branch or "", environment or "",
        )
        now = _now()
        async with self._connect() as database:
            await database.execute(
                """
                INSERT INTO memory_entities(
                    id,scope_type,owner_id,space_id,domain_id,entity_type,
                    canonical_name,normalized_name,branch,environment,status,
                    created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,'active',?,?)
                ON CONFLICT(id) DO UPDATE SET
                    domain_id=excluded.domain_id,
                    canonical_name=excluded.canonical_name,
                    status='active',updated_at=excluded.updated_at
                """,
                (
                    entity_id, scope_type, owner_id.strip(), space_id.strip(), domain_id,
                    entity_type.strip(), " ".join(canonical_name.split())[:300], normalized,
                    branch, environment, now, now,
                ),
            )
            for alias in aliases:
                normalized_alias = self._normalize(alias)
                if normalized_alias:
                    await database.execute(
                        """
                        INSERT OR IGNORE INTO memory_entity_aliases(
                            entity_id,alias,normalized_alias,created_at
                        ) VALUES(?,?,?,?)
                        """,
                        (entity_id, " ".join(alias.split())[:300], normalized_alias, now),
                    )
            await database.commit()
        return await self.get_entity(entity_id)

    async def get_entity(self, entity_id: str) -> MemoryEntity:
        async with self._connect() as database:
            row = await (
                await database.execute(
                    "SELECT * FROM memory_entities WHERE id=? AND status='active'",
                    (entity_id,),
                )
            ).fetchone()
            aliases = await (
                await database.execute(
                    "SELECT alias FROM memory_entity_aliases WHERE entity_id=? ORDER BY alias",
                    (entity_id,),
                )
            ).fetchall()
        if row is None:
            raise KeyError(entity_id)
        return MemoryEntity(
            id=row["id"], scope_type=row["scope_type"], owner_id=row["owner_id"],
            space_id=row["space_id"], domain_id=row["domain_id"],
            entity_type=row["entity_type"], canonical_name=row["canonical_name"],
            branch=row["branch"], environment=row["environment"],
            aliases=tuple(item["alias"] for item in aliases),
        )

    async def upsert_relation(
        self,
        *,
        source_entity_id: str,
        target_entity_id: str,
        relation_type: str,
        summary: str,
        evidence: Iterable[tuple[str, str]],
        confidence: float,
    ) -> str:
        if not 0 <= confidence <= 1:
            raise ValueError("relation confidence must be between zero and one")
        relation_id = _stable_id(
            "relation", source_entity_id, target_entity_id, relation_type
        )
        now = _now()
        async with self._connect() as database:
            await database.execute(
                """
                INSERT INTO memory_entity_relations(
                    id,source_entity_id,target_entity_id,relation_type,summary,
                    confidence,status,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,'active',?,?)
                ON CONFLICT(id) DO UPDATE SET
                    summary=excluded.summary,confidence=excluded.confidence,
                    status='active',updated_at=excluded.updated_at
                """,
                (
                    relation_id, source_entity_id, target_entity_id,
                    relation_type[:100], " ".join(summary.split())[:1000],
                    confidence, now, now,
                ),
            )
            for source_type, source_id in evidence:
                if source_type and source_id:
                    await database.execute(
                        """
                        INSERT OR IGNORE INTO memory_entity_evidence(
                            relation_id,source_type,source_id,created_at
                        ) VALUES(?,?,?,?)
                        """,
                        (relation_id, source_type[:50], source_id[:500], now),
                    )
            await database.commit()
        return relation_id

    async def search(
        self,
        query: str,
        *,
        scope_type: str,
        owner_id: str,
        space_id: str,
        domain_id: str | None = None,
        branch: str | None = None,
        environment: str | None = None,
        limit: int = 5,
    ) -> list[EntityRelationMatch]:
        clauses = [
            "s.scope_type=?", "s.owner_id=?", "s.space_id=?",
            "s.status='active'", "t.status='active'", "r.status='active'",
        ]
        values: list[object] = [scope_type, owner_id, space_id]
        if domain_id:
            clauses.append("(s.domain_id=? OR s.domain_id IS NULL)")
            values.append(domain_id)
        if branch:
            clauses.append("(s.branch=? OR s.branch IS NULL)")
            values.append(branch)
        if environment:
            clauses.append("(s.environment=? OR s.environment IS NULL)")
            values.append(environment)
        sql = f"""
            SELECT r.*,s.canonical_name AS source_name,s.entity_type AS source_type,
                   s.branch,s.environment,t.canonical_name AS target_name,
                   t.entity_type AS target_type
            FROM memory_entity_relations r
            JOIN memory_entities s ON s.id=r.source_entity_id
            JOIN memory_entities t ON t.id=r.target_entity_id
            WHERE {' AND '.join(clauses)}
            ORDER BY r.confidence DESC,r.updated_at DESC
            LIMIT 500
        """
        async with self._connect() as database:
            rows = await (await database.execute(sql, values)).fetchall()
            output: list[EntityRelationMatch] = []
            terms = [self._normalize(item) for item in query.split() if self._normalize(item)]
            for row in rows:
                aliases = await (
                    await database.execute(
                        """
                        SELECT alias FROM memory_entity_aliases
                        WHERE entity_id IN (?,?)
                        """,
                        (row["source_entity_id"], row["target_entity_id"]),
                    )
                ).fetchall()
                haystack = self._normalize(" ".join(
                    [row["source_name"], row["target_name"], row["summary"]]
                    + [item["alias"] for item in aliases]
                ))
                if terms and not any(term in haystack for term in terms):
                    continue
                evidence = await (
                    await database.execute(
                        """
                        SELECT source_type,source_id FROM memory_entity_evidence
                        WHERE relation_id=? ORDER BY source_type,source_id
                        """,
                        (row["id"],),
                    )
                ).fetchall()
                output.append(EntityRelationMatch(
                    relation_id=row["id"], relation_type=row["relation_type"],
                    source_name=row["source_name"], source_type=row["source_type"],
                    target_name=row["target_name"], target_type=row["target_type"],
                    summary=row["summary"], branch=row["branch"],
                    environment=row["environment"], confidence=float(row["confidence"]),
                    evidence_ids=tuple(item["source_id"] for item in evidence),
                    evidence_refs=tuple(
                        (item["source_type"], item["source_id"]) for item in evidence
                    ),
                ))
                if len(output) >= max(1, min(limit, 20)):
                    break
        return output

    @staticmethod
    def _normalize(value: str) -> str:
        return "".join(str(value).casefold().split())[:500]


class PostgresEntityMemoryRepository(EntityMemoryRepository):
    def __init__(self, database_resources: DatabaseResources):
        self.database_resources = database_resources

    @asynccontextmanager
    async def _connect(self):
        async with PostgresCompatConnection(self.database_resources) as connection:
            yield connection

    async def initialize(self) -> None:
        if not await self.database_resources.check_ready():
            raise RuntimeError("PostgreSQL entity-memory repository is unavailable")
