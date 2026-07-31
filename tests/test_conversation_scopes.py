from datetime import UTC, datetime
from pathlib import Path

import pytest

from knowledge.agent_runtime.conversation_scopes import (
    ConversationScopeConflictError,
    ConversationScopeRepository,
    PostgresConversationScopeRepository,
)


@pytest.mark.asyncio
async def test_conversation_scope_is_persistent_and_idempotent(tmp_path: Path):
    db_path = tmp_path / "agent.db"
    first_repository = ConversationScopeRepository(db_path)
    await first_repository.initialize()

    created = await first_repository.bind(
        "conversation-1",
        "middle-platform",
        "metric-platform",
    )
    repeated = await first_repository.bind(
        "conversation-1",
        "middle-platform",
        "metric-platform",
    )
    restarted_repository = ConversationScopeRepository(db_path)
    await restarted_repository.initialize()

    assert repeated == created
    assert await restarted_repository.get("conversation-1") == created


@pytest.mark.asyncio
async def test_conversation_scope_rejects_switch_and_can_be_deleted(tmp_path: Path):
    repository = ConversationScopeRepository(tmp_path / "agent.db")
    await repository.initialize()
    await repository.bind("conversation-1", "middle-platform", "metric-platform")

    with pytest.raises(ConversationScopeConflictError):
        await repository.bind("conversation-1", "middle-platform", "workflow")

    assert await repository.delete("conversation-1") is True
    assert await repository.get("conversation-1") is None
    assert await repository.delete("conversation-1") is False


@pytest.mark.asyncio
async def test_conversation_scope_validates_identifiers(tmp_path: Path):
    repository = ConversationScopeRepository(tmp_path / "agent.db")
    await repository.initialize()

    with pytest.raises(ValueError):
        await repository.bind("", "middle-platform", None)
    with pytest.raises(ValueError):
        await repository.bind("conversation-1", "", None)


@pytest.mark.asyncio
async def test_postgres_scope_binding_uses_one_atomic_statement():
    row = {
        "conversation_id": "conversation-1",
        "knowledge_space_id": "middle-platform",
        "domain_id": "approval-flow",
        "created_at": datetime.now(UTC),
    }
    statements = []

    class Result:
        def mappings(self):
            return self

        def one(self):
            return row

        def one_or_none(self):
            return row

    class Connection:
        async def execute(self, statement):
            statements.append(statement)
            return Result()

    class Transaction:
        async def __aenter__(self):
            return Connection()

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    class Resources:
        def transaction(self):
            return Transaction()

    repository = PostgresConversationScopeRepository(Resources())

    scope = await repository.bind(
        "conversation-1", "middle-platform", "approval-flow"
    )

    assert scope.conversation_id == "conversation-1"
    assert len(statements) == 1
