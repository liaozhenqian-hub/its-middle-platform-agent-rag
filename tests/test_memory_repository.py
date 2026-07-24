from datetime import UTC, datetime, timedelta

import pytest
import aiosqlite

from knowledge.memory.models import MemoryCandidateCreate
from knowledge.memory.repository import MemoryRepository


@pytest.mark.asyncio
async def test_memory_repository_initializes_and_approves_candidates(tmp_path):
    repository = MemoryRepository(tmp_path / "agent_memory.db")
    await repository.initialize()

    candidate = await repository.create_candidate(
        MemoryCandidateCreate(
            scope_type="user",
            owner_id="u-1",
            space_id="middle-platform",
            domain_id="approval-flow",
            memory_type="user_preference",
            subject="answer_format",
            normalized_fact="回答接口问题时包含入参和出参",
            summary="用户偏好接口回答包含入参和出参",
            source_turn_id="turn-1",
            source_citations=("chunk-1",),
            confidence=0.92,
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
    )

    assert candidate.status == "candidate"
    approved = await repository.approve_candidate(candidate.id)
    assert approved.status == "confirmed"
    memories = await repository.list_memories(
        scope_type="user", owner_id="u-1", statuses=("confirmed",)
    )
    assert len(memories) == 1
    assert memories[0].normalized_fact == "回答接口问题时包含入参和出参"


@pytest.mark.asyncio
async def test_memory_repository_conflicts_supersede_previous_fact(tmp_path):
    repository = MemoryRepository(tmp_path / "agent_memory.db")
    await repository.initialize()
    common = dict(
        scope_type="user",
        owner_id="u-1",
        space_id="middle-platform",
        domain_id=None,
        memory_type="user_context",
        subject="default_branch",
        source_turn_id="turn",
        source_citations=(),
        confidence=0.9,
        expires_at=None,
    )
    first = await repository.create_candidate(
        MemoryCandidateCreate(
            **common,
            normalized_fact="开发环境使用 develop",
            summary="开发环境默认使用 develop",
        )
    )
    await repository.approve_candidate(first.id)
    second = await repository.create_candidate(
        MemoryCandidateCreate(
            **common,
            normalized_fact="开发环境使用 test",
            summary="开发环境默认使用 test",
        )
    )
    approved = await repository.approve_candidate(second.id)

    memories = await repository.list_memories(
        scope_type="user", owner_id="u-1", statuses=("confirmed", "expired")
    )
    assert approved.status == "confirmed"
    assert {item.status for item in memories} == {"confirmed", "expired"}
    assert any(item.normalized_fact == "开发环境使用 develop" and item.status == "expired" for item in memories)


@pytest.mark.asyncio
async def test_memory_repository_hides_expired_and_deleted_records(tmp_path):
    repository = MemoryRepository(tmp_path / "agent_memory.db")
    await repository.initialize()
    candidate = await repository.create_candidate(
        MemoryCandidateCreate(
            scope_type="domain",
            owner_id="approval-flow",
            space_id="middle-platform",
            domain_id="approval-flow",
            memory_type="decision_memory",
            subject="transfer",
            normalized_fact="管理员转办接口只读",
            summary="管理员转办能力是只读查询",
            source_turn_id="turn-1",
            source_citations=(),
            confidence=0.8,
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
    )
    await repository.approve_candidate(candidate.id)

    assert await repository.expire_memories() == 1
    assert await repository.search_memories(
        "管理员转办", scope_type="domain", owner_id="approval-flow"
    ) == []

    current = await repository.create_candidate(
        MemoryCandidateCreate(
            scope_type=candidate.scope_type,
            owner_id=candidate.owner_id,
            space_id=candidate.space_id,
            domain_id=candidate.domain_id,
            memory_type=candidate.memory_type,
            subject=candidate.subject,
            normalized_fact=candidate.normalized_fact,
            summary=candidate.summary,
            source_turn_id=candidate.source_turn_id,
            source_citations=candidate.source_citations,
            confidence=candidate.confidence,
            expires_at=None,
        )
    )
    await repository.approve_candidate(current.id)
    assert await repository.soft_delete_memory(current.id) is True
    assert await repository.get_memory(current.id) is None


@pytest.mark.asyncio
async def test_repository_lists_only_due_user_memory_candidates(tmp_path):
    path = tmp_path / "agent_memory.db"
    repository = MemoryRepository(path)
    await repository.initialize()
    now = datetime.now(UTC)

    async def candidate(scope_type: str, owner_id: str, subject: str):
        return await repository.create_candidate(
            MemoryCandidateCreate(
                scope_type=scope_type,
                owner_id=owner_id,
                space_id="middle-platform",
                domain_id="approval-flow" if scope_type == "domain" else None,
                memory_type="user_context" if scope_type == "user" else "decision_memory",
                subject=subject,
                normalized_fact=subject,
                summary=subject,
                source_turn_id="turn",
                source_citations=(),
                confidence=0.9,
                expires_at=now + timedelta(days=7),
            )
        )

    due = await candidate("user", "user-1", "due-user")
    await candidate("user", "user-1", "fresh-user")
    await candidate("domain", "approval-flow", "due-domain")
    async with aiosqlite.connect(path) as db:
        old = (now - timedelta(hours=25)).isoformat()
        await db.execute(
            "UPDATE memory_candidates SET created_at=? WHERE subject IN (?,?)",
            (old, "due-user", "due-domain"),
        )
        await db.commit()

    records = await repository.list_due_user_candidates(
        now - timedelta(hours=24), limit=20
    )

    assert [item.id for item in records] == [due.id]


@pytest.mark.asyncio
async def test_repository_auto_confirmation_due_list_excludes_explicit_review_types(tmp_path):
    path = tmp_path / "agent_memory.db"
    repository = MemoryRepository(path)
    await repository.initialize()
    now = datetime.now(UTC)
    created = []
    for memory_type in (
        "user_preference", "user_context", "decision_memory",
        "episodic_memory", "procedural_memory",
    ):
        created.append(await repository.create_candidate(MemoryCandidateCreate(
            scope_type="user", owner_id="user-1", space_id="middle-platform",
            domain_id="approval-flow", memory_type=memory_type,
            subject=memory_type, normalized_fact=memory_type, summary=memory_type,
            source_turn_id="turn", confidence=0.9,
            expires_at=now + timedelta(days=7),
        )))
    async with aiosqlite.connect(path) as database:
        await database.execute(
            "UPDATE memory_candidates SET created_at=?",
            ((now - timedelta(hours=25)).isoformat(),),
        )
        await database.commit()

    due = await repository.list_due_user_candidates(now - timedelta(hours=24))

    assert {item.memory_type for item in due} == {"user_preference", "user_context"}
