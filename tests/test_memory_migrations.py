import aiosqlite
import pytest

from knowledge.memory.models import MemoryCandidateCreate
from knowledge.memory.repository import MemoryRepository


@pytest.mark.asyncio
async def test_memory_migrations_are_idempotent_and_preserve_existing_rows(tmp_path):
    path = tmp_path / "agent_memory.db"
    repository = MemoryRepository(path)
    await repository.initialize()
    candidate = await repository.create_candidate(MemoryCandidateCreate(
        scope_type="user", owner_id="user-1", space_id="middle-platform",
        domain_id=None, memory_type="user_preference", subject="format",
        normalized_fact="回答保持简洁", summary="用户偏好简洁回答",
        source_turn_id="turn-1", confidence=0.9,
    ))

    await repository.initialize()

    async with aiosqlite.connect(path) as database:
        versions = await (await database.execute(
            "SELECT version FROM memory_schema_migrations ORDER BY version"
        )).fetchall()
        row = await (await database.execute(
            "SELECT subject FROM memory_candidates WHERE id=?", (candidate.id,)
        )).fetchone()
        procedure_table = await (await database.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_procedural_specs'"
        )).fetchone()

    assert versions == [(1,), (2,), (3,)]
    assert row == ("format",)
    assert procedure_table == ("memory_procedural_specs",)
