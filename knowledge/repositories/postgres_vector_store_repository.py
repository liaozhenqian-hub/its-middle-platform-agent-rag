from __future__ import annotations

from hashlib import sha256
from contextlib import contextmanager
import re
from typing import Any
from uuid import uuid4

from pgvector.psycopg import register_vector
from pgvector import Vector
from psycopg import sql
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from knowledge.config.settings import Settings, get_settings
from knowledge.schemas.documents import KeywordIndexRecord, KnowledgeChunk, SearchResult


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_COLUMN_MAP = {
    "app_id": "app_id",
    "domain": "domain",
    "domain_id": "domain",
    "source_id": "source_id",
    "source_type": "source_type",
    "branch": "branch",
    "owner_id": "owner_id",
    "scope_type": "scope_type",
    "space_id": "space_id",
    "enabled": "enabled",
    "chunk_id": "id",
}


class _PostgresVectorBulkWriter:
    def __init__(
        self,
        *,
        repository: "PostgresVectorStoreRepository",
        connection: Any,
        cursor: Any,
        staging_table: str,
    ) -> None:
        self.repository = repository
        self.connection = connection
        self.cursor = cursor
        self.staging_table = staging_table

    def upsert_with_embeddings(
        self,
        chunks: list[KnowledgeChunk],
        embeddings: list[list[float]],
    ) -> list[str]:
        return self.repository._write_bulk_batch(
            connection=self.connection,
            cursor=self.cursor,
            staging_table=self.staging_table,
            chunks=chunks,
            embeddings=embeddings,
        )


class PostgresVectorStoreRepository:
    """Synchronous pgvector adapter matching the existing Chroma contract."""

    def __init__(
        self,
        *,
        pool: Any,
        collection_name: str,
        embedding: Any | None,
        schema: str = "public",
        table: str = "vector_entries",
        batch_size: int = 500,
        dimensions: int = 1024,
        hnsw_ef_search: int = 100,
        memory_collection_name: str = "middle_platform_memories",
        owns_pool: bool = False,
    ) -> None:
        if not collection_name.strip():
            raise ValueError("collection_name is required")
        if not _IDENTIFIER.fullmatch(schema) or not _IDENTIFIER.fullmatch(table):
            raise ValueError("Invalid PostgreSQL schema or table identifier")
        self.pool = pool
        self.collection_name = collection_name.strip()
        self.embedding = embedding
        self.schema = schema
        self.table = table
        self.batch_size = batch_size
        self.dimensions = dimensions
        self.hnsw_ef_search = hnsw_ef_search
        self.memory_collection_name = memory_collection_name
        self._owns_pool = owns_pool
        self._qualified_table = sql.SQL("{}.{}").format(
            sql.Identifier(schema),
            sql.Identifier(table),
        )

    def __repr__(self) -> str:
        return (
            f"PostgresVectorStoreRepository(collection_name={self.collection_name!r}, "
            f"schema={self.schema!r}, table={self.table!r})"
        )

    @classmethod
    def from_settings(
        cls,
        settings: Settings | None = None,
        *,
        collection_name: str | None = None,
        embedding: Any | None = None,
    ) -> "PostgresVectorStoreRepository":
        resolved = settings or get_settings()
        pool = ConnectionPool(
            conninfo=resolved.resolved_psycopg_url,
            min_size=1,
            max_size=resolved.database_pool_size + resolved.database_max_overflow,
            timeout=resolved.database_pool_timeout_seconds,
            kwargs={"autocommit": False},
            configure=register_vector,
            open=True,
        )
        pool.wait(timeout=resolved.database_pool_timeout_seconds)
        return cls(
            pool=pool,
            collection_name=collection_name or resolved.chroma_collection_name,
            embedding=embedding,
            schema=resolved.pgvector_schema,
            table=resolved.pgvector_table,
            batch_size=resolved.pgvector_batch_size,
            dimensions=resolved.pgvector_dimensions,
            hnsw_ef_search=resolved.pgvector_hnsw_ef_search,
            memory_collection_name=resolved.memory_chroma_collection_name,
            owns_pool=True,
        )

    def close(self) -> None:
        if self._owns_pool:
            self.pool.close()
            self._owns_pool = False

    def reset(self) -> None:
        query = sql.SQL("DELETE FROM {} WHERE collection_name = %s").format(
            self._qualified_table
        )
        with self.pool.connection() as connection:
            connection.execute(query, (self.collection_name,))

    def upsert(self, chunks: list[KnowledgeChunk]) -> list[str]:
        if not chunks:
            return []
        if self.embedding is None:
            raise ValueError("An embedding provider is required for vector upsert")
        embeddings = self.embedding.embed_documents([chunk.content for chunk in chunks])
        return self.upsert_with_embeddings(chunks, embeddings)

    def upsert_with_embeddings(
        self,
        chunks: list[KnowledgeChunk],
        embeddings: list[list[float]],
    ) -> list[str]:
        self._validate_embedding_batch(chunks, embeddings)
        if not chunks:
            return []
        with self.bulk_import() as writer:
            return writer.upsert_with_embeddings(chunks, embeddings)

    @contextmanager
    def bulk_import(self):
        staging_table = f"vector_migration_{uuid4().hex}"
        create_staging = sql.SQL(
            "CREATE TEMP TABLE {} (LIKE {} INCLUDING DEFAULTS) ON COMMIT PRESERVE ROWS"
        ).format(sql.Identifier(staging_table), self._qualified_table)
        drop_staging = sql.SQL("DROP TABLE IF EXISTS {}").format(
            sql.Identifier(staging_table)
        )
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(create_staging)
                connection.commit()
                writer = _PostgresVectorBulkWriter(
                    repository=self,
                    connection=connection,
                    cursor=cursor,
                    staging_table=staging_table,
                )
                try:
                    yield writer
                finally:
                    try:
                        cursor.execute(drop_staging)
                        connection.commit()
                    except Exception:
                        connection.rollback()

    def _validate_embedding_batch(
        self,
        chunks: list[KnowledgeChunk],
        embeddings: list[list[float]],
    ) -> None:
        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings must have the same length")
        for embedding in embeddings:
            if len(embedding) != self.dimensions:
                raise ValueError(
                    f"embedding must contain exactly {self.dimensions} values"
                )

    def _write_bulk_batch(
        self,
        *,
        connection: Any,
        cursor: Any,
        staging_table: str,
        chunks: list[KnowledgeChunk],
        embeddings: list[list[float]],
    ) -> list[str]:
        self._validate_embedding_batch(chunks, embeddings)
        if not chunks:
            return []
        columns = (
            "collection_name", "id", "content", "heading", "metadata", "embedding",
            "content_hash", "app_id", "domain", "source_id", "source_type", "branch",
            "owner_id", "scope_type", "space_id", "enabled",
        )
        copy_statement = sql.SQL("COPY {} ({}) FROM STDIN").format(
            sql.Identifier(staging_table),
            sql.SQL(", ").join(map(sql.Identifier, columns)),
        )
        statement = sql.SQL(
            """
            INSERT INTO {} (
                collection_name, id, content, heading, metadata, embedding,
                content_hash, app_id, domain, source_id, source_type, branch,
                owner_id, scope_type, space_id, enabled
            ) SELECT {} FROM {}
            ON CONFLICT (collection_name, id) DO UPDATE SET
                content = EXCLUDED.content,
                heading = EXCLUDED.heading,
                metadata = EXCLUDED.metadata,
                embedding = EXCLUDED.embedding,
                content_hash = EXCLUDED.content_hash,
                app_id = EXCLUDED.app_id,
                domain = EXCLUDED.domain,
                source_id = EXCLUDED.source_id,
                source_type = EXCLUDED.source_type,
                branch = EXCLUDED.branch,
                owner_id = EXCLUDED.owner_id,
                scope_type = EXCLUDED.scope_type,
                space_id = EXCLUDED.space_id,
                enabled = EXCLUDED.enabled,
                updated_at = now()
            """
        ).format(
            self._qualified_table,
            sql.SQL(", ").join(map(sql.Identifier, columns)),
            sql.Identifier(staging_table),
        )
        rows = [
            self._row(chunk, embedding)
            for chunk, embedding in zip(chunks, embeddings)
        ]
        try:
            cursor.execute(
                sql.SQL("TRUNCATE TABLE {}").format(sql.Identifier(staging_table))
            )
            with cursor.copy(copy_statement) as copy:
                for row in rows:
                    copy.write_row(row)
            cursor.execute(statement)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        return [chunk.chunk_id for chunk in chunks]

    def update_metadata(self, chunks: list[KnowledgeChunk]) -> list[str]:
        if not chunks:
            return []
        statement = sql.SQL(
            """
            UPDATE {} SET
                metadata = %s,
                content_hash = %s,
                app_id = %s,
                domain = %s,
                source_id = %s,
                source_type = %s,
                branch = %s,
                owner_id = %s,
                scope_type = %s,
                space_id = %s,
                enabled = %s,
                updated_at = now()
            WHERE collection_name = %s AND id = %s
            """
        ).format(self._qualified_table)
        rows = []
        for chunk in chunks:
            metadata = dict(chunk.metadata)
            normalized = self._normalized_columns(metadata)
            rows.append(
                (
                    Jsonb(metadata),
                    str(metadata.get("content_hash") or sha256(chunk.content.encode("utf-8")).hexdigest()),
                    normalized["app_id"],
                    normalized["domain"],
                    normalized["source_id"],
                    normalized["source_type"],
                    normalized["branch"],
                    normalized["owner_id"],
                    normalized["scope_type"],
                    normalized["space_id"],
                    normalized["enabled"],
                    self.collection_name,
                    chunk.chunk_id,
                )
            )
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.executemany(statement, rows)
        return [chunk.chunk_id for chunk in chunks]

    def delete(self, chunk_ids: list[str]) -> int:
        ids = list(dict.fromkeys(item for item in chunk_ids if item))
        if not ids:
            return 0
        statement = sql.SQL(
            "DELETE FROM {} WHERE collection_name = %s AND id = ANY(%s)"
        ).format(self._qualified_table)
        with self.pool.connection() as connection:
            connection.execute(statement, (self.collection_name, ids))
        return len(ids)

    def search(
        self,
        query: str,
        k: int = 5,
        where: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        if k < 1:
            raise ValueError("k must be at least 1")
        if self.embedding is None:
            raise ValueError("An embedding provider is required for vector search")
        self._validate_memory_scope(where)
        query_embedding = self.embedding.embed_query(query)
        if len(query_embedding) != self.dimensions:
            raise ValueError(
                f"embedding must contain exactly {self.dimensions} values"
            )
        where_sql, where_params = self._where_sql(where)
        statement = sql.SQL(
            """
            SELECT id, content, heading, metadata, embedding <=> %s::vector AS distance
            FROM {}
            WHERE collection_name = %s AND ({})
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """
        ).format(self._qualified_table, sql.SQL(where_sql))
        params = [
            query_embedding,
            self.collection_name,
            *where_params,
            query_embedding,
            k,
        ]
        with self.pool.connection() as connection:
            connection.execute(
                "SELECT set_config('hnsw.ef_search', %s, true)",
                (str(self.hnsw_ef_search),),
            )
            connection.execute(
                "SELECT set_config('hnsw.iterative_scan', 'strict_order', true)"
            )
            rows = connection.execute(statement, params).fetchall()
        return [
            SearchResult(
                chunk_id=str(row[0]),
                content=str(row[1]),
                metadata=dict(row[3] or {}),
                score=float(row[4]),
            )
            for row in rows
        ]

    def get_chunks(
        self,
        where: dict[str, Any] | None = None,
        ids: list[str] | None = None,
    ) -> list[KnowledgeChunk]:
        where_sql, params = self._where_sql(where)
        clauses = ["collection_name = %s", f"({where_sql})"]
        all_params: list[Any] = [self.collection_name, *params]
        if ids is not None:
            clauses.append("id = ANY(%s)")
            all_params.append(list(ids))
        statement = sql.SQL(
            "SELECT id, content, heading, metadata FROM {} WHERE {} ORDER BY id"
        ).format(self._qualified_table, sql.SQL(" AND ".join(clauses)))
        with self.pool.connection() as connection:
            rows = connection.execute(statement, all_params).fetchall()
        return [
            KnowledgeChunk(
                chunk_id=str(row[0]),
                content=str(row[1]),
                heading=str(row[2] or ""),
                metadata=dict(row[3] or {}),
            )
            for row in rows
        ]

    def get_chunk_ids(self, where: dict[str, Any] | None = None) -> set[str]:
        where_sql, params = self._where_sql(where)
        statement = sql.SQL(
            "SELECT id FROM {} WHERE collection_name = %s AND ({})"
        ).format(self._qualified_table, sql.SQL(where_sql))
        with self.pool.connection() as connection:
            rows = connection.execute(
                statement,
                [self.collection_name, *params],
            ).fetchall()
        return {str(row[0]) for row in rows}

    def get_keyword_index_records(
        self,
        where: dict[str, Any] | None = None,
    ) -> list[KeywordIndexRecord]:
        where_sql, params = self._where_sql(where)
        statement = sql.SQL(
            "SELECT id, heading, metadata FROM {} "
            "WHERE collection_name = %s AND ({}) ORDER BY id"
        ).format(self._qualified_table, sql.SQL(where_sql))
        records: list[KeywordIndexRecord] = []
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(statement, [self.collection_name, *params])
                while True:
                    rows = cursor.fetchmany(2000)
                    if not rows:
                        break
                    for record_id, heading, raw_metadata in rows:
                        metadata = dict(raw_metadata or {})
                        records.append(
                            KeywordIndexRecord(
                                chunk_id=str(metadata.get("chunk_id") or record_id),
                                heading=str(heading or metadata.get("heading", "")),
                                keywords=str(metadata.get("bm25_keywords", "")),
                                metadata=metadata,
                            )
                        )
        return records

    def count(self) -> int:
        statement = sql.SQL(
            "SELECT count(*) FROM {} WHERE collection_name = %s"
        ).format(self._qualified_table)
        with self.pool.connection() as connection:
            return int(
                connection.execute(statement, (self.collection_name,)).fetchone()[0]
            )

    def build_hnsw_index(self) -> None:
        index_name = f"ix_{self.table}_embedding_hnsw"
        statement = sql.SQL(
            "CREATE INDEX IF NOT EXISTS {} ON {} "
            "USING hnsw (embedding vector_cosine_ops)"
        ).format(sql.Identifier(index_name), self._qualified_table)
        with self.pool.connection() as connection:
            connection.execute(statement)

    def analyze(self) -> None:
        with self.pool.connection() as connection:
            connection.execute(sql.SQL("ANALYZE {}").format(self._qualified_table))

    def _row(
        self,
        chunk: KnowledgeChunk,
        embedding: list[float],
    ) -> tuple[Any, ...]:
        metadata = dict(chunk.metadata)
        normalized = self._normalized_columns(metadata)
        return (
            self.collection_name,
            chunk.chunk_id,
            chunk.content,
            chunk.heading,
            Jsonb(metadata),
            Vector(embedding),
            str(metadata.get("content_hash") or sha256(chunk.content.encode("utf-8")).hexdigest()),
            normalized["app_id"],
            normalized["domain"],
            normalized["source_id"],
            normalized["source_type"],
            normalized["branch"],
            normalized["owner_id"],
            normalized["scope_type"],
            normalized["space_id"],
            normalized["enabled"],
        )

    @staticmethod
    def _normalized_columns(metadata: dict[str, Any]) -> dict[str, Any]:
        enabled = metadata.get("enabled", True)
        if isinstance(enabled, str):
            enabled = enabled.strip().lower() not in {"false", "0", "no", "disabled"}
        return {
            "app_id": PostgresVectorStoreRepository._text(metadata.get("app_id")),
            "domain": PostgresVectorStoreRepository._text(
                metadata.get("domain") or metadata.get("domain_id")
            ),
            "source_id": PostgresVectorStoreRepository._text(metadata.get("source_id")),
            "source_type": PostgresVectorStoreRepository._text(metadata.get("source_type")),
            "branch": PostgresVectorStoreRepository._text(metadata.get("branch")),
            "owner_id": PostgresVectorStoreRepository._text(metadata.get("owner_id")),
            "scope_type": PostgresVectorStoreRepository._text(metadata.get("scope_type")),
            "space_id": PostgresVectorStoreRepository._text(metadata.get("space_id")),
            "enabled": bool(enabled),
        }

    @staticmethod
    def _text(value: Any) -> str | None:
        return None if value is None or str(value) == "" else str(value)

    def _where_sql(
        self,
        where: dict[str, Any] | None,
    ) -> tuple[str, list[Any]]:
        if not where:
            return "TRUE", []
        if set(where) == {"$and"}:
            return self._logical_where("AND", where["$and"])
        if set(where) == {"$or"}:
            return self._logical_where("OR", where["$or"])
        clauses: list[str] = []
        params: list[Any] = []
        for key, value in where.items():
            if key.startswith("$"):
                raise ValueError(f"Unsupported metadata operator: {key}")
            column = _COLUMN_MAP.get(key)
            if column is not None:
                clauses.append(f'"{column}" = %s')
                params.append(value)
            else:
                clauses.append("metadata -> %s = %s")
                params.extend((key, Jsonb(value)))
        return " AND ".join(clauses) or "TRUE", params

    def _logical_where(
        self,
        operator: str,
        items: Any,
    ) -> tuple[str, list[Any]]:
        if not isinstance(items, list) or not items:
            raise ValueError(f"${operator.lower()} requires a non-empty list")
        clauses: list[str] = []
        params: list[Any] = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("metadata filter clauses must be objects")
            clause, item_params = self._where_sql(item)
            clauses.append(f"({clause})")
            params.extend(item_params)
        return f" {operator} ".join(clauses), params

    def _validate_memory_scope(self, where: dict[str, Any] | None) -> None:
        if self.collection_name != self.memory_collection_name:
            return
        keys = self._where_keys(where)
        required = {"owner_id", "scope_type", "space_id"}
        if not required.issubset(keys):
            raise ValueError(
                "memory search requires owner_id, scope_type, and space_id filters"
            )

    @classmethod
    def _where_keys(cls, where: dict[str, Any] | None) -> set[str]:
        if not where:
            return set()
        keys = {key for key in where if not key.startswith("$")}
        for key in ("$and", "$or"):
            items = where.get(key)
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        keys.update(cls._where_keys(item))
        return keys
