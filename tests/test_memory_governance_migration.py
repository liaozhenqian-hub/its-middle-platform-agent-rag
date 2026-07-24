import aiosqlite
import pytest

from knowledge.migrations.memory_governance import migrate_memory_governance
from knowledge.memory.models import MemoryCandidateCreate
from knowledge.memory.repository import MemoryRepository


@pytest.mark.asyncio
async def test_memory_governance_migration_dry_run_and_apply_are_idempotent(tmp_path):
    path = tmp_path / "memory.db"
    repository = MemoryRepository(path)
    await repository.initialize()
    candidate = await repository.create_candidate(MemoryCandidateCreate(
        scope_type="user", owner_id="user-1", space_id="middle-platform",
        domain_id="approval-flow", memory_type="procedural_memory",
        subject="legacy", normalized_fact="legacy steps", summary="旧流程",
        source_turn_id="run", source_citations=("code-1",), confidence=0.8,
    ))

    dry = await migrate_memory_governance(path, apply=False)
    async with aiosqlite.connect(path) as database:
        before = await (await database.execute(
            "SELECT legacy_format FROM memory_candidates WHERE id=?", (candidate.id,)
        )).fetchone()
    applied = await migrate_memory_governance(path, apply=True)
    repeated = await migrate_memory_governance(path, apply=True)
    async with aiosqlite.connect(path) as database:
        after = await (await database.execute(
            "SELECT legacy_format FROM memory_candidates WHERE id=?", (candidate.id,)
        )).fetchone()

    assert dry["legacy_procedures"] == 1
    assert before == (None,)
    assert applied["legacy_procedures"] == 1
    assert repeated["legacy_procedures"] == 0
    assert after == ("legacy-v1",)
