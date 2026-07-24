from datetime import UTC, datetime, timedelta

import pytest

from knowledge.memory.models import MemoryCandidateCreate, ProceduralSpec, ProceduralStep
from knowledge.memory.repository import MemoryRepository
from knowledge.memory.service import MemoryService


@pytest.mark.asyncio
async def test_domain_promotion_creates_separate_reviewed_domain_memory(tmp_path):
    repository = MemoryRepository(tmp_path / "memory.db")
    await repository.initialize()
    service = MemoryService(repository, default_retention_days=180)
    candidate = await repository.create_candidate(MemoryCandidateCreate(
        scope_type="user", owner_id="user-1", space_id="middle-platform",
        domain_id="approval-flow", memory_type="procedural_memory",
        subject="bug-diagnosis:approval-flow", normalized_fact="structured-v2",
        summary="个人审批流排障流程", source_turn_id="run-1",
        source_citations=("code-1",), confidence=0.9,
    ))
    spec = ProceduralSpec(
        task_type="bug_diagnosis", procedure_version=2,
        trigger_conditions=("trace_id_present",), required_inputs=("environment", "trace_id"),
        environment_constraints=("develop",), branch_constraints=("develop",),
        steps=(ProceduralStep("query_trace_logs", "查询脱敏日志"),),
        allowed_tools=("query_trace_logs",), minimum_evidence_grade="correlated",
        stop_conditions=("no_logs",), fallback_actions=("request_more_context",),
        expected_output=("validation_steps",), validation_steps=("verify_current_code",),
    )
    await repository.upsert_procedural_spec(candidate.id, spec)
    personal = await service.approve_candidate(candidate.id, actor="user:user-1")

    promotion = await service.request_domain_promotion(
        source_memory_id=personal.id, target_domain_id="approval-flow",
        public_summary="审批流开发环境标准排障流程", requested_by="admin",
        valid_until=datetime.now(UTC) + timedelta(days=90),
    )
    domain = await service.approve_domain_promotion(promotion.id, actor="admin")

    assert domain.scope_type == "domain"
    assert domain.owner_id == "approval-flow"
    assert domain.id != personal.id
    assert (await repository.get_memory(personal.id)).scope_type == "user"
    assert await repository.get_procedural_spec(domain.id) == spec


@pytest.mark.asyncio
async def test_domain_promotion_rejects_unsupported_or_evidence_free_memory(tmp_path):
    repository = MemoryRepository(tmp_path / "memory.db")
    await repository.initialize()
    service = MemoryService(repository)
    candidate = await repository.create_candidate(MemoryCandidateCreate(
        scope_type="user", owner_id="user-1", space_id="middle-platform",
        domain_id=None, memory_type="user_preference", subject="format",
        normalized_fact="简洁", summary="简洁", source_turn_id="turn-1",
        source_citations=(), confidence=0.9,
    ))
    memory = await service.approve_candidate(candidate.id)

    with pytest.raises(ValueError, match="not eligible"):
        await service.request_domain_promotion(
            source_memory_id=memory.id, target_domain_id="approval-flow",
            public_summary="领域格式", requested_by="admin", valid_until=None,
        )
