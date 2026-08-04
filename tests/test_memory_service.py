from datetime import UTC, datetime, timedelta

import pytest
import aiosqlite

from knowledge.memory.models import MemoryCandidateCreate
from knowledge.memory.repository import MemoryRepository
from knowledge.memory.service import MemoryService


@pytest.mark.asyncio
async def test_memory_recall_skips_vector_search_when_no_visible_records():
    class EmptyRepository:
        async def list_memories(self, **kwargs):
            return []

    class CountingIndex:
        def __init__(self):
            self.calls = 0

        def search(self, *args, **kwargs):
            self.calls += 1
            return []

    index = CountingIndex()
    service = MemoryService(EmptyRepository(), index=index)

    result = await service.recall(
        "审批接口",
        user_id="user-without-memory",
        space_id="middle-platform",
        domain_id="approval-flow",
    )

    assert result == []
    assert index.calls == 0


@pytest.mark.asyncio
async def test_memory_service_recall_only_returns_confirmed_visible_records(tmp_path):
    repository = MemoryRepository(tmp_path / "memory.db")
    await repository.initialize()
    service = MemoryService(repository, max_recall=5)
    candidate = await repository.create_candidate(
        MemoryCandidateCreate(
            scope_type="user",
            owner_id="u-1",
            space_id="middle-platform",
            domain_id="approval-flow",
            memory_type="user_preference",
            subject="answer",
            normalized_fact="接口回答要包含入参和出参",
            summary="用户偏好接口回答包含入参和出参",
            source_turn_id="turn-1",
            source_citations=("chunk-1",),
            confidence=0.9,
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
    )

    assert await service.recall(
        "接口入参", user_id="u-1", space_id="middle-platform", domain_id="approval-flow"
    ) == []
    await repository.approve_candidate(candidate.id)
    results = await service.recall(
        "接口入参", user_id="u-1", space_id="middle-platform", domain_id="approval-flow"
    )
    assert [item.summary for item in results] == ["用户偏好接口回答包含入参和出参"]
@pytest.mark.asyncio
async def test_service_approval_uses_confirmed_retention_not_candidate_ttl(tmp_path):
    repository = MemoryRepository(tmp_path / "retention.db")
    await repository.initialize()
    service = MemoryService(repository, default_retention_days=180)
    candidate = await repository.create_candidate(MemoryCandidateCreate(
        scope_type="user",
        owner_id="u-1",
        space_id="middle-platform",
        domain_id=None,
        memory_type="user_preference",
        subject="format",
        normalized_fact="使用简洁格式",
        summary="用户偏好简洁格式",
        source_turn_id="turn-1",
        confidence=0.9,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    ))

    memory = await service.approve_candidate(candidate.id)

    assert memory.valid_until > datetime.now(UTC) + timedelta(days=179)


@pytest.mark.asyncio
async def test_service_auto_confirms_due_user_candidates_and_indexes_them(tmp_path):
    path = tmp_path / "memory.db"
    repository = MemoryRepository(path)
    await repository.initialize()
    candidate = await repository.create_candidate(
        MemoryCandidateCreate(
            scope_type="user",
            owner_id="user-1",
            space_id="middle-platform",
            domain_id=None,
            memory_type="user_preference",
            subject="answer_format",
            normalized_fact="回答时包含接口示例",
            summary="用户偏好回答包含接口示例",
            source_turn_id="turn-1",
            source_citations=(),
            confidence=0.9,
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
    )
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "UPDATE memory_candidates SET created_at=? WHERE id=?",
            ((datetime.now(UTC) - timedelta(hours=25)).isoformat(), candidate.id),
        )
        await db.commit()

    class FakeIndex:
        def __init__(self):
            self.ids = []

        def upsert(self, memory):
            self.ids.append(memory.id)

    index = FakeIndex()
    service = MemoryService(
        repository,
        index=index,
        auto_confirm_seconds=24 * 3600,
    )

    confirmed = await service.auto_confirm_due_candidates()

    assert [item.id for item in confirmed] == [candidate.id]
    assert index.ids == [candidate.id]
    assert (await repository.get_candidate(candidate.id)).status == "approved"
