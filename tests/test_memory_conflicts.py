from datetime import UTC, datetime, timedelta

import pytest

from knowledge.memory.models import MemoryCandidateCreate, ProceduralSpec, ProceduralStep
from knowledge.memory.repository import MemoryRepository
from knowledge.memory.service import MemoryService


async def confirmed_procedure(repository):
    candidate = await repository.create_candidate(MemoryCandidateCreate(
        scope_type="user", owner_id="user-1", space_id="middle-platform",
        domain_id="approval-flow", memory_type="procedural_memory",
        subject="bug", normalized_fact="v2", summary="排障流程",
        source_turn_id="run", source_citations=("code-1",), confidence=0.9,
        expires_at=datetime.now(UTC) + timedelta(days=7),
    ))
    await repository.upsert_procedural_spec(candidate.id, ProceduralSpec(
        task_type="bug_diagnosis", procedure_version=2,
        trigger_conditions=("trace_id_present",), required_inputs=("trace_id",),
        environment_constraints=("develop",), branch_constraints=("develop",),
        steps=(ProceduralStep("query_trace_logs", "查询脱敏日志"),),
        allowed_tools=("query_trace_logs",), minimum_evidence_grade="correlated",
        stop_conditions=("no_logs",), fallback_actions=("stop",),
        expected_output=("answer",), validation_steps=("verify",),
    ))
    return await MemoryService(repository).approve_candidate(candidate.id)


@pytest.mark.asyncio
async def test_repeated_conflicts_remove_memory_from_recall_without_deleting_it(tmp_path):
    repository = MemoryRepository(tmp_path / "memory.db")
    await repository.initialize()
    memory = await confirmed_procedure(repository)
    service = MemoryService(repository)

    await service.record_memory_conflict(memory.id, "runtime_contradiction", threshold=2)
    assert len(await service.recall_procedures(
        user_id="user-1", domain_id="approval-flow", task_type="bug_diagnosis",
        environment="develop", branch="develop",
    )) == 1
    await service.record_memory_conflict(memory.id, "contract_changed", threshold=2)

    assert await service.recall_procedures(
        user_id="user-1", domain_id="approval-flow", task_type="bug_diagnosis",
        environment="develop", branch="develop",
    ) == []
    assert await repository.get_memory(memory.id) is not None


@pytest.mark.asyncio
async def test_index_failure_is_queued_for_repair_after_sqlite_confirmation(tmp_path):
    class BrokenIndex:
        def upsert(self, memory):
            raise RuntimeError("index unavailable")

    repository = MemoryRepository(tmp_path / "memory.db")
    await repository.initialize()
    candidate = await repository.create_candidate(MemoryCandidateCreate(
        scope_type="user", owner_id="user-1", space_id="middle-platform",
        domain_id=None, memory_type="user_preference", subject="format",
        normalized_fact="简洁", summary="简洁", source_turn_id="turn",
        confidence=0.9,
    ))

    memory = await MemoryService(repository, index=BrokenIndex()).approve_candidate(candidate.id)

    assert memory.status == "confirmed"
    assert await repository.list_index_repairs() == [(memory.id, "upsert")]
