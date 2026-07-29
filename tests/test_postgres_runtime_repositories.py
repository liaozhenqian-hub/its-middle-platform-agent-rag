import asyncio
import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import psycopg
from psycopg import sql
import pytest
import pytest_asyncio

from knowledge.agent_runtime.conversation_scopes import (
    ConversationScopeConflictError,
    PostgresConversationScopeRepository,
)
from knowledge.agent_runtime.pending_runs import (
    PendingRunConflictError,
    PostgresPendingRunRepository,
)
from knowledge.config.settings import Settings
from knowledge.feishu.repository import PostgresFeishuEventRepository
from knowledge.persistence.database import DatabaseResources
from knowledge.persistence.factory import RelationalRepositoryFactory
from knowledge.auth.repository import PostgresUserAuthRepository
from knowledge.catalog.repository import PostgresCatalogRepository
from knowledge.catalog.models import KnowledgeSourceCreate, SourceType
from knowledge.memory.repository import PostgresMemoryRepository
from knowledge.memory.entities import PostgresEntityMemoryRepository
from knowledge.quality.repository import PostgresQualityRepository
from knowledge.quality.models import TurnStart
from knowledge.history.service import PostgresConversationHistoryService


def test_relational_repository_factory_selects_postgres_runtime_repositories(tmp_path):
    resources = object()
    factory = RelationalRepositoryFactory(
        provider="postgres",
        database_resources=resources,
    )

    assert isinstance(factory.pending_runs(tmp_path / "agent.db"), PostgresPendingRunRepository)
    assert isinstance(factory.conversation_scopes(tmp_path / "agent.db"), PostgresConversationScopeRepository)
    assert isinstance(factory.feishu_events(tmp_path / "feishu.db"), PostgresFeishuEventRepository)
    assert isinstance(factory.user_auth(tmp_path / "auth.db"), PostgresUserAuthRepository)
    assert isinstance(factory.catalog(tmp_path / "catalog.db"), PostgresCatalogRepository)
    assert isinstance(factory.memory(tmp_path / "memory.db"), PostgresMemoryRepository)
    assert isinstance(factory.memory_entities(tmp_path / "memory.db"), PostgresEntityMemoryRepository)
    assert isinstance(factory.quality(tmp_path / "quality.db"), PostgresQualityRepository)
    assert isinstance(
        factory.history(object(), tmp_path / "agent.db"),
        PostgresConversationHistoryService,
    )


def test_relational_repository_factory_requires_database_resources_for_postgres():
    with pytest.raises(ValueError, match="database_resources"):
        RelationalRepositoryFactory(provider="postgres")


@pytest_asyncio.fixture
async def postgres_runtime_repositories():
    if os.getenv("RUN_POSTGRES_INTEGRATION") != "1":
        pytest.skip("requires the configured dev PostgreSQL")
    schema = "runtime_repos_test_" + uuid4().hex[:12]
    settings = Settings(DATA_STORE_PROVIDER="postgres", DATABASE_SCHEMA=schema)
    config = Config(str(Path("alembic.ini").resolve()))
    config.attributes["database_url"] = settings.resolved_psycopg_url
    config.attributes["database_schema"] = schema
    with psycopg.connect(settings.resolved_psycopg_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    resources = DatabaseResources(settings)
    try:
        command.upgrade(config, "head")
        await resources.start()
        yield resources
    finally:
        await resources.close()
        try:
            command.downgrade(config, "base")
        finally:
            with psycopg.connect(settings.resolved_psycopg_url, autocommit=True) as connection:
                connection.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


@pytest.mark.live
@pytest.mark.asyncio
async def test_postgres_pending_run_matches_state_and_conflict_semantics(postgres_runtime_repositories):
    repository = PostgresPendingRunRepository(postgres_runtime_repositories)
    await repository.initialize()
    await repository.save_pending("run-1", "conversation-1", {"step": 1}, [{"id": "approval-1"}])

    restored = await repository.get_pending("run-1")
    assert restored.state == {"step": 1}
    assert restored.approvals == [{"id": "approval-1"}]

    await repository.mark_completed("run-1")
    with pytest.raises(PendingRunConflictError):
        await repository.get_pending("run-1")


@pytest.mark.live
@pytest.mark.asyncio
async def test_postgres_conversation_scope_is_idempotent_and_rejects_switch(postgres_runtime_repositories):
    repository = PostgresConversationScopeRepository(postgres_runtime_repositories)
    created = await repository.bind("conversation-1", "middle-platform", "approval-flow")
    assert await repository.bind("conversation-1", "middle-platform", "approval-flow") == created
    with pytest.raises(ConversationScopeConflictError):
        await repository.bind("conversation-1", "middle-platform", "workflow")
    assert await repository.delete("conversation-1") is True


@pytest.mark.live
@pytest.mark.asyncio
async def test_postgres_feishu_claim_is_atomic_and_retry_is_bounded(postgres_runtime_repositories):
    repository = PostgresFeishuEventRepository(postgres_runtime_repositories)
    await repository.initialize()
    claims = await asyncio.gather(*(
        repository.claim("event-1", "message-1", "chat-1") for _ in range(5)
    ))
    assert claims.count(True) == 1
    await repository.fail("event-1", "TimeoutError")
    assert await repository.claim("event-1", "message-1", "chat-1") is True
    await repository.fail("event-1", "RuntimeError")
    assert await repository.claim("event-1", "message-1", "chat-1") is False


@pytest.mark.live
@pytest.mark.asyncio
async def test_postgres_quality_can_start_a_turn(postgres_runtime_repositories):
    repository = PostgresQualityRepository(postgres_runtime_repositories)
    await repository.initialize()

    turn = await repository.start_turn(
        TurnStart(
            run_id="run-1",
            conversation_id="conversation-1",
            channel="codex",
            question="hello",
            provider="deepseek",
            model_name="flash",
        )
    )

    assert turn.run_id == "run-1"
    assert turn.status == "running"


@pytest.mark.live
@pytest.mark.asyncio
async def test_postgres_auth_preserves_identity_and_conversation_isolation(postgres_runtime_repositories):
    repository = PostgresUserAuthRepository(postgres_runtime_repositories)
    await repository.initialize()
    user = await repository.upsert_feishu_user(
        open_id="open-1",
        tenant_key="tenant-1",
        display_name="User One",
        avatar_url=None,
    )
    owner = await repository.bind_conversation_owner(
        "conversation-1",
        user.open_id,
        channel="web",
    )
    assert owner.owner_id == "open-1"
    with pytest.raises(PermissionError):
        await repository.bind_conversation_owner(
            "conversation-1",
            "open-2",
            channel="web",
        )


@pytest.mark.live
@pytest.mark.asyncio
async def test_postgres_catalog_claim_uses_skip_locked_without_duplicate_jobs(postgres_runtime_repositories):
    repository = PostgresCatalogRepository(postgres_runtime_repositories)
    await repository.initialize()
    await repository.create_source(KnowledgeSourceCreate(
        id="source-1",
        space_id="middle-platform",
        domain_id="approval-flow",
        source_type=SourceType.DOCUMENT,
        name="Approval docs",
    ))
    await repository.enqueue_job(source_id="source-1", kind="manual")
    await repository.enqueue_job(source_id="source-1", kind="scheduled")

    claimed = await asyncio.gather(
        repository.claim_next_job("worker-a"),
        repository.claim_next_job("worker-b"),
    )

    assert all(job is not None for job in claimed)
    assert len({job.id for job in claimed}) == 2
