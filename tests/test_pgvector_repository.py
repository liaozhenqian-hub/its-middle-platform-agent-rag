import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import psycopg
from psycopg import sql
import pytest
from pgvector import Vector

from knowledge.config.settings import Settings
from knowledge.repositories.postgres_vector_store_repository import (
    PostgresVectorStoreRepository,
)
import knowledge.repositories.postgres_vector_store_repository as pgvector_module
from knowledge.schemas.documents import KnowledgeChunk


class FixedEmbeddings:
    def __init__(self, query_vector):
        self.query_vector = query_vector
        self.document_calls = 0
        self.query_calls = 0

    def embed_documents(self, documents):
        self.document_calls += 1
        return [list(self.query_vector) for _ in documents]

    def embed_query(self, _query):
        self.query_calls += 1
        return list(self.query_vector)


def vector(first=0.0, second=0.0):
    return [first, second, *([0.0] * 1022)]


def chunk(chunk_id, content, **metadata):
    return KnowledgeChunk(
        chunk_id=chunk_id,
        heading=metadata.pop("heading", chunk_id),
        content=content,
        metadata={"chunk_id": chunk_id, **metadata},
    )


def test_embedding_dimension_is_validated_before_database_access():
    repository = PostgresVectorStoreRepository(
        pool=object(),
        collection_name="knowledge",
        embedding=None,
        dimensions=1024,
    )

    with pytest.raises(ValueError, match="1024"):
        repository.upsert_with_embeddings([chunk("a", "A")], [[0.1, 0.2]])


def test_pgvector_domain_column_prefers_stable_domain_id():
    columns = PostgresVectorStoreRepository._normalized_columns(
        {
            "domain": "审批流",
            "domain_id": "approval-flow",
            "source_type": "product_document",
        }
    )

    assert columns["domain"] == "approval-flow"


def test_pgvector_pool_keeps_warmed_connections_for_configured_idle_window(monkeypatch):
    captured = {}

    class Pool:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def wait(self, timeout):
            captured["wait_timeout"] = timeout

    monkeypatch.setattr(pgvector_module, "ConnectionPool", Pool)
    settings = Settings(
        _env_file=None,
        VECTOR_STORE_PROVIDER="pgvector",
        DATABASE_URL="postgresql://agent:secret@db.internal/middle_agent",
        DATABASE_POOL_SIZE=1,
        DATABASE_MAX_OVERFLOW=0,
        PGVECTOR_POOL_MAX_IDLE_SECONDS=300,
    )

    PostgresVectorStoreRepository.from_settings(settings, embedding=None)

    assert captured["min_size"] == 0
    assert captured["max_size"] == 1
    assert captured["max_idle"] == 300.0


def test_vector_upsert_uses_copy_staging_without_embedding_api_calls():
    captured = []

    class Copy:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def write_row(self, row): captured.append(("row", row[1], row[5]))

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def execute(self, statement): captured.append(("execute", statement.as_string()))
        def copy(self, statement):
            captured.append(("copy", statement.as_string()))
            return Copy()

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def cursor(self): return Cursor()
        def commit(self): captured.append(("commit", None))
        def rollback(self): captured.append(("rollback", None))

    class Pool:
        def connection(self): return Connection()

    repository = PostgresVectorStoreRepository(
        pool=Pool(), collection_name="knowledge", embedding=None
    )

    assert repository.upsert_with_embeddings([chunk("a", "A")], [vector(1, 0)]) == ["a"]
    assert any(item[0] == "copy" for item in captured)
    assert any("ON CONFLICT" in item[1] for item in captured if item[0] == "execute")
    copied = next(item for item in captured if item[0] == "row")
    assert isinstance(copied[2], Vector)


def test_hnsw_build_and_analyze_use_identifier_safe_ddl():
    statements = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, statement, params=None):
            statements.append((statement.as_string() if hasattr(statement, "as_string") else str(statement), params))

    class Pool:
        def connection(self):
            return Connection()

    repository = PostgresVectorStoreRepository(
        pool=Pool(),
        collection_name="knowledge",
        embedding=None,
        schema="safe_schema",
        table="vector_entries",
    )

    repository.build_hnsw_index()
    repository.analyze()

    assert "safe_schema" in statements[0][0]
    assert "vector_cosine_ops" in statements[0][0]
    assert statements[1][0].startswith("ANALYZE")


def test_keyword_index_reads_lightweight_metadata_projection():
    statements = []

    class Cursor:
        def __init__(self):
            self.pages = [[(
                "chunk-1",
                "管理员转办",
                "管理员 转办 adminTransfer",
                {
                    "app_id": "middle-platform",
                    "domain_id": "approval-flow",
                    "source_type": "code",
                    "branch": "develop",
                },
            )], []]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, statement, params):
            statements.append((statement.as_string(), params))

        def fetchmany(self, _size):
            return self.pages.pop(0)

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def cursor(self):
            return Cursor()

    class Pool:
        def connection(self):
            return Connection()

    repository = PostgresVectorStoreRepository(
        pool=Pool(), collection_name="knowledge", embedding=None
    )

    records = repository.get_keyword_index_records(
        where={"app_id": "middle-platform"}
    )

    assert records[0].keywords == "管理员 转办 adminTransfer"
    assert records[0].metadata == {
        "app_id": "middle-platform",
        "domain_id": "approval-flow",
        "source_type": "code",
        "branch": "develop",
    }
    assert "metadata ->> 'bm25_keywords'" in statements[0][0]
    assert "SELECT id, heading, metadata FROM" not in statements[0][0]


@pytest.mark.live
@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
    reason="requires the configured dev PostgreSQL",
)
def test_pgvector_repository_contract_and_collection_isolation():
    schema = "agent_vector_test_" + uuid4().hex[:12]
    settings = Settings(
        DATA_STORE_PROVIDER="postgres",
        DATABASE_SCHEMA=schema,
        PGVECTOR_SCHEMA=schema,
    )
    config = Config(str(Path("alembic.ini").resolve()))
    config.attributes["database_url"] = settings.resolved_psycopg_url
    config.attributes["database_schema"] = schema

    with psycopg.connect(settings.resolved_psycopg_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

    try:
        command.upgrade(config, "head")
        embeddings = FixedEmbeddings(vector(1.0, 0.0))
        repository = PostgresVectorStoreRepository.from_settings(
            settings,
            collection_name="metric_platform_knowledge",
            embedding=embeddings,
        )
        memories = PostgresVectorStoreRepository.from_settings(
            settings,
            collection_name="middle_platform_memories",
            embedding=embeddings,
        )
        try:
            source_chunks = [
                chunk(
                    "same-id",
                    "审批流管理员转办接口",
                    app_id="middle-platform",
                    domain="审批流",
                    branch="develop",
                    source_type="code",
                    chunk_type="faq",
                ),
                chunk(
                    "other-id",
                    "指标平台说明",
                    app_id="middle-platform",
                    domain="指标平台",
                    branch="master",
                    source_type="product_document",
                    chunk_type="guide",
                ),
            ]
            repository.upsert_with_embeddings(
                source_chunks,
                [vector(1.0, 0.0), vector(0.0, 1.0)],
            )
            assert embeddings.document_calls == 0
            assert repository.count() == 2
            assert repository.get_chunk_ids(where={"chunk_type": "faq"}) == {
                "same-id"
            }

            results = repository.search(
                "管理员转办",
                k=5,
                where={
                    "$and": [
                        {"app_id": "middle-platform"},
                        {"branch": "develop"},
                    ]
                },
            )
            assert [result.chunk_id for result in results] == ["same-id"]
            assert results[0].score == pytest.approx(0.0)
            assert embeddings.query_calls == 1

            updated = chunk(
                "same-id",
                "ignored-content",
                app_id="middle-platform",
                domain="审批流",
                branch="develop",
                source_type="code",
                chunk_type="api_contract",
            )
            assert repository.update_metadata([updated]) == ["same-id"]
            stored = repository.get_chunks(ids=["same-id"])
            assert stored[0].content == "审批流管理员转办接口"
            assert stored[0].metadata["chunk_type"] == "api_contract"

            memories.upsert_with_embeddings(
                [
                    chunk(
                        "same-id",
                        "用户偏好",
                        source_type="memory",
                        owner_id="user-a",
                        scope_type="personal",
                        space_id="middle-platform",
                        domain_id="审批流",
                    )
                ],
                [vector(1.0, 0.0)],
            )
            assert memories.count() == 1
            assert repository.count() == 2
            with pytest.raises(ValueError, match="owner_id.*scope_type.*space_id"):
                memories.search("偏好", k=5)
            memory_results = memories.search(
                "偏好",
                k=5,
                where={
                    "$and": [
                        {"source_type": "memory"},
                        {"owner_id": "user-a"},
                        {"scope_type": "personal"},
                        {"space_id": "middle-platform"},
                    ]
                },
            )
            assert [item.chunk_id for item in memory_results] == ["same-id"]

            assert repository.delete(["same-id", "missing"]) == 2
            assert repository.count() == 1
        finally:
            repository.close()
            memories.close()
        command.downgrade(config, "base")
    finally:
        with psycopg.connect(settings.resolved_psycopg_url, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(schema)
                )
            )
