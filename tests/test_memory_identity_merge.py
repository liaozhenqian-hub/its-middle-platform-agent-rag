from datetime import UTC, datetime, timedelta

import pytest

from knowledge.memory.models import MemoryCandidateCreate
from knowledge.memory.repository import MemoryRepository


async def _confirmed(
    repository: MemoryRepository,
    *,
    owner: str,
    subject: str,
    fact: str,
):
    candidate = await repository.create_candidate(
        MemoryCandidateCreate(
            scope_type="user",
            owner_id=owner,
            space_id="middle-platform",
            domain_id="approval-flow",
            memory_type="user_context",
            subject=subject,
            normalized_fact=fact,
            summary=fact,
            source_turn_id=None,
            confidence=0.9,
            expires_at=datetime.now(UTC) + timedelta(days=30),
        )
    )
    return await repository.approve_candidate(candidate.id)


@pytest.mark.asyncio
async def test_memory_owner_merge_previews_and_handles_unique_duplicate_conflict(tmp_path):
    repository = MemoryRepository(tmp_path / "memory.db")
    await repository.initialize()
    source = "anon:source"
    target = "ou_target"
    await _confirmed(repository, owner=target, subject="branch", fact="master")
    await _confirmed(repository, owner=target, subject="format", fact="include schemas")
    await _confirmed(repository, owner=source, subject="branch", fact="develop")
    await _confirmed(repository, owner=source, subject="format", fact="include schemas")
    await _confirmed(repository, owner=source, subject="language", fact="Chinese")
    pending = await repository.create_candidate(
        MemoryCandidateCreate(
            scope_type="user",
            owner_id=source,
            space_id="middle-platform",
            domain_id=None,
            memory_type="user_preference",
            subject="tone",
            normalized_fact="concise",
            summary="concise",
            source_turn_id=None,
            confidence=0.8,
        )
    )

    preview = await repository.preview_user_owner_merge(source, target)
    result = await repository.merge_user_owner(source, target)
    repeated = await repository.merge_user_owner(source, target)

    target_memories = await repository.list_memories(
        scope_type="user", owner_id=target, statuses=("confirmed",), limit=100
    )
    target_candidates = await repository.list_candidates(
        status="candidate", owner_id=target, limit=100
    )
    source_memories = await repository.list_memories(
        scope_type="user", owner_id=source, statuses=("confirmed",), limit=100
    )

    assert preview == {
        "memories": 3,
        "candidates": 1,
        "duplicates": 1,
        "conflicts": 1,
        "unique": 1,
    }
    assert result["moved_memories"] == 1
    assert result["deduplicated_memories"] == 1
    assert result["conflicting_memories"] == 1
    assert result["moved_candidates"] == 1
    assert repeated == {
        "moved_memories": 0,
        "deduplicated_memories": 0,
        "conflicting_memories": 0,
        "moved_candidates": 0,
        "conversation_summaries": 0,
        "extraction_jobs": 0,
    }
    assert {item.normalized_fact for item in target_memories} == {
        "master",
        "include schemas",
        "Chinese",
    }
    assert {item.normalized_fact for item in target_candidates} == {
        "develop",
        pending.normalized_fact,
    }
    assert source_memories == []
