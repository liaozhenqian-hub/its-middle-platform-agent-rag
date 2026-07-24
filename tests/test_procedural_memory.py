import json
from datetime import UTC, datetime, timedelta

import pytest

from knowledge.memory.models import MemoryCandidateCreate, ProceduralSpec, ProceduralStep
from knowledge.memory.procedures import ProceduralMemoryValidator
from knowledge.memory.repository import MemoryRepository
from knowledge.memory.service import MemoryService


@pytest.mark.asyncio
async def test_procedural_memory_requires_confirmation_and_recalls_bounded_steps(tmp_path):
    repository = MemoryRepository(tmp_path / "memory.db")
    await repository.initialize()
    steps = [
        "确认环境并映射代码分支",
        "按 trace ID 查询脱敏日志",
        "根据异常符号检索代码",
        "结合引用证据验证修复",
    ]
    candidate = await repository.create_candidate(MemoryCandidateCreate(
        scope_type="user",
        owner_id="user-1",
        space_id="middle-platform",
        domain_id="approval-flow",
        memory_type="procedural_memory",
        subject="bug-diagnosis:approval-flow",
        normalized_fact=json.dumps({"steps": steps}, ensure_ascii=False),
        summary="审批流 Bug 标准排障流程：" + "；".join(steps),
        source_turn_id="run-1",
        source_citations=("code-1",),
        confidence=0.9,
        expires_at=datetime.now(UTC) + timedelta(days=7),
    ))
    service = MemoryService(repository)

    assert await service.recall(
        "排障流程",
        user_id="user-1",
        space_id="middle-platform",
        domain_id="approval-flow",
    ) == []
    confirmed = await service.approve_candidate(candidate.id)
    recalled = await service.recall(
        "排障流程",
        user_id="user-1",
        space_id="middle-platform",
        domain_id="approval-flow",
    )

    assert confirmed.memory_type == "procedural_memory"
    assert [item.id for item in recalled] == [candidate.id]
    assert "trace ID" in recalled[0].summary


@pytest.mark.asyncio
async def test_structured_procedure_round_trips_and_matches_environment_branch(tmp_path):
    repository = MemoryRepository(tmp_path / "memory.db")
    await repository.initialize()
    candidate = await repository.create_candidate(MemoryCandidateCreate(
        scope_type="user", owner_id="user-1", space_id="middle-platform",
        domain_id="approval-flow", memory_type="procedural_memory",
        subject="bug-diagnosis:approval-flow", normalized_fact="structured-v2",
        summary="审批流 Bug 排障流程", source_turn_id="run-1",
        source_citations=("code-1",), confidence=0.9,
    ))
    spec = ProceduralSpec(
        task_type="bug_diagnosis", procedure_version=2,
        trigger_conditions=("trace_id_present",), required_inputs=("environment", "trace_id"),
        environment_constraints=("develop",), branch_constraints=("develop",),
        steps=(ProceduralStep("query_trace_logs", "查询脱敏日志", ("trace_id",), ("exception",)),),
        allowed_tools=("query_trace_logs",), minimum_evidence_grade="correlated",
        stop_conditions=("no_logs",), fallback_actions=("request_more_context",),
        expected_output=("confirmed_facts", "validation_steps"),
        validation_steps=("verify_current_code",),
    )
    await repository.upsert_procedural_spec(candidate.id, spec)
    await MemoryService(repository).approve_candidate(candidate.id)

    matches = await repository.list_matching_procedures(
        owner_id="user-1", domain_id="approval-flow", task_type="bug_diagnosis",
        environment="develop", branch="develop", limit=3,
    )
    mismatches = await repository.list_matching_procedures(
        owner_id="user-1", domain_id="approval-flow", task_type="bug_diagnosis",
        environment="prod", branch="master", limit=3,
    )

    assert matches[0][1] == spec
    assert mismatches == []


def test_structured_procedure_rejects_sensitive_or_uncontrolled_steps():
    validator = ProceduralMemoryValidator()
    unsafe = ProceduralSpec(
        task_type="bug_diagnosis", procedure_version=2,
        trigger_conditions=("trace_id_present",), required_inputs=("trace_id",),
        environment_constraints=("develop",), branch_constraints=("develop",),
        steps=(ProceduralStep("curl", "Authorization: Bearer secret", (), ()),),
        allowed_tools=("curl",), minimum_evidence_grade="correlated",
        stop_conditions=("no_logs",), fallback_actions=("stop",),
        expected_output=("answer",), validation_steps=("verify",),
    )

    with pytest.raises(ValueError, match="unsafe procedural memory"):
        validator.validate(unsafe)
