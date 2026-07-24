from datetime import UTC, datetime, timedelta

import pytest

from knowledge.memory.models import MemoryCandidateCreate
from knowledge.memory.repository import MemoryRepository


@pytest.mark.asyncio
async def test_memory_cleanup_is_dry_run_by_default_and_purges_on_apply(tmp_path):
    repository = MemoryRepository(tmp_path / "memory.db")
    await repository.initialize()
    candidate = await repository.create_candidate(MemoryCandidateCreate(
        scope_type="user",
        owner_id="user-1",
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
    await repository.approve_candidate(candidate.id)
    await repository.soft_delete_memory(candidate.id)

    preview = await repository.cleanup_terminal_records(apply=False)
    assert preview["memories"] == 1
    assert preview["candidates"] == 1
    assert (await repository.get_candidate(candidate.id)).status == "approved"
    assert await repository.get_memory(candidate.id) is None

    applied = await repository.cleanup_terminal_records(apply=True)
    assert applied == preview
    with pytest.raises(KeyError):
        await repository.get_candidate(candidate.id)
