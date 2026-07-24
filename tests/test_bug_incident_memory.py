from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from knowledge.bug_graph.service import BugDiagnosisGraphService, BugDiagnosisResult
from knowledge.logs.grafana import LogEntry, StackFrame, TraceLogResult
from knowledge.memory.incidents import BugIncidentMemoryRecorder
from knowledge.memory.entities import EntityMemoryRepository
from knowledge.memory.repository import MemoryRepository


@pytest.mark.asyncio
async def test_incident_recorder_creates_only_evidence_backed_sanitized_candidate(tmp_path):
    repository = MemoryRepository(tmp_path / "memory.db")
    await repository.initialize()
    recorder = BugIncidentMemoryRecorder(repository, candidate_ttl_seconds=3600)
    state = {
        "run_id": "run-1",
        "environment": "develop",
        "service": "approval-service",
        "endpoint": "/transfer",
        "normalized_problem": "traceId trace-secret-123456 管理员转办报错",
        "trace_id": "trace-secret-123456",
        "exception_types": ["NullPointerException"],
        "domain_hints": ["approval-flow"],
    }
    result = BugDiagnosisResult(
        status="completed",
        answer="Bearer hidden-token 原始诊断正文",
        missing_fields=[],
        citations=[
            {
                "source_type": "log_trace",
                "source_id": "trace-secret-123456",
                "metadata": {"log_count": 3},
            },
            {
                "source_type": "code",
                "source_id": "code-1",
                "domain": "审批流",
                "metadata": {"branch": "develop", "relative_path": "Transfer.java"},
            },
        ],
        evidence_grade="correlated",
    )

    candidate = await recorder.record("user-1", state, result)
    low_evidence = await recorder.record(
        "user-1",
        state,
        BugDiagnosisResult(
            status="completed",
            answer="仅日志",
            missing_fields=[],
            citations=[],
            evidence_grade="log_only",
        ),
    )

    assert candidate is not None
    assert candidate.memory_type == "episodic_memory"
    assert candidate.scope_type == "user"
    assert candidate.owner_id == "user-1"
    assert candidate.domain_id == "approval-flow"
    assert candidate.source_citations == ("code-1",)
    assert "develop" in candidate.normalized_fact
    assert "approval-service" in candidate.normalized_fact
    assert "trace-secret" not in candidate.normalized_fact + candidate.summary
    assert "hidden-token" not in candidate.normalized_fact + candidate.summary
    assert low_evidence is None


@pytest.mark.asyncio
async def test_incident_recorder_builds_evidence_backed_service_endpoint_relation(tmp_path):
    repository = MemoryRepository(tmp_path / "memory.db")
    entities = EntityMemoryRepository(tmp_path / "memory.db")
    await repository.initialize()
    await entities.initialize()
    recorder = BugIncidentMemoryRecorder(
        repository,
        candidate_ttl_seconds=3600,
        entity_repository=entities,
    )
    result = BugDiagnosisResult(
        status="completed",
        answer="诊断完成",
        missing_fields=[],
        citations=[{
            "source_type": "code",
            "source_id": "code-1",
            "domain": "审批流",
            "metadata": {"branch": "develop"},
        }],
        evidence_grade="correlated",
    )

    await recorder.record("user-1", {
        "run_id": "run-entity",
        "environment": "develop",
        "service": "approval-service",
        "endpoint": "/transfer",
        "exception_types": ["IllegalStateException"],
        "domain_hints": ["approval-flow"],
    }, result)
    relations = await entities.search(
        "approval-service transfer",
        scope_type="user",
        owner_id="user-1",
        space_id="middle-platform",
        branch="develop",
    )

    assert relations[0].relation_type == "serves_endpoint"
    assert relations[0].evidence_ids == ("code-1",)


@pytest.mark.asyncio
async def test_incident_recorder_also_creates_confirmable_procedural_candidate(tmp_path):
    repository = MemoryRepository(tmp_path / "memory.db")
    await repository.initialize()
    recorder = BugIncidentMemoryRecorder(
        repository,
        candidate_ttl_seconds=3600,
        procedural_enabled=True,
    )
    result = BugDiagnosisResult(
        status="completed",
        answer="诊断完成",
        missing_fields=[],
        citations=[{"source_type": "code", "source_id": "code-1", "domain": "审批流"}],
        evidence_grade="correlated",
    )

    await recorder.record("user-1", {
        "run_id": "run-procedure",
        "environment": "develop",
        "service": "approval-service",
        "endpoint": "/transfer",
        "exception_types": ["IllegalStateException"],
        "domain_hints": ["approval-flow"],
    }, result)
    candidates = await repository.list_candidates(
        status="candidate",
        scope_type="user",
        owner_id="user-1",
        memory_type="procedural_memory",
    )

    assert len(candidates) == 1
    assert "查询脱敏日志" in candidates[0].summary
    assert "思考过程" not in candidates[0].summary
    spec = await repository.get_procedural_spec(candidates[0].id)
    assert spec is not None
    assert spec.procedure_version == 2
    assert spec.allowed_tools == (
        "query_trace_logs", "extract_log_signals", "search_branch_code",
        "inspect_contract_and_docs", "validate_fix",
    )


class _LogClient:
    async def query_trace(self, trace_id, environment, time_range_minutes, *, now_ms=None):
        return TraceLogResult(
            trace_id=trace_id,
            environment=environment,
            code_branch="develop",
            from_ms=1,
            to_ms=2,
            entries=(LogEntry(
                timestamp="1",
                level="ERROR",
                logger="TransferService",
                message="sanitized NullPointerException",
                exception_types=("NullPointerException",),
                stack_frames=(StackFrame("TransferService.run", "TransferService.java", 42),),
            ),),
            exception_types=("NullPointerException",),
        )


class _CodeRetriever:
    async def search(self, state, log_result):
        return [{
            "chunk_id": "code-1",
            "heading": "TransferService.run",
            "content": "sanitized code excerpt",
            "domain": "审批流",
            "metadata": {
                "branch": "develop",
                "relative_path": "TransferService.java",
                "symbol_name": "TransferService.run",
            },
            "match_type": "symbol_exact",
            "rerank_score": 0.9,
        }]


class _Recorder:
    def __init__(self):
        self.calls = []

    async def record(self, user_id, state, result):
        self.calls.append((user_id, dict(state), result))


@pytest.mark.asyncio
async def test_bug_graph_records_completed_correlated_incident_for_current_user(tmp_path):
    recorder = _Recorder()
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "graph.db")) as saver:
        service = BugDiagnosisGraphService(
            checkpointer=saver,
            log_client=_LogClient(),
            code_retriever=_CodeRetriever(),
            incident_recorder=recorder,
        )
        result = await service.diagnose(
            "开发环境 traceId: trace-incident-123456 管理员转办报错",
            conversation_id="conversation-1",
            run_id="run-1",
            user_id="user-1",
        )

    assert result.evidence_grade == "correlated"
    assert recorder.calls[0][0] == "user-1"
    assert recorder.calls[0][2].status == "completed"


@pytest.mark.asyncio
async def test_bug_graph_uses_current_users_entity_relations_as_code_search_hints(tmp_path):
    class EntityRepository:
        def __init__(self):
            self.calls = []

        async def search(self, query, **kwargs):
            self.calls.append((query, kwargs))
            return [SimpleNamespace(
                source_name="approval-service",
                target_name="/transfer",
                summary="审批服务提供管理员转办接口",
            )]

    class CapturingRetriever(_CodeRetriever):
        def __init__(self):
            self.states = []

        async def search(self, state, log_result):
            self.states.append(dict(state))
            return await super().search(state, log_result)

    entities = EntityRepository()
    retriever = CapturingRetriever()
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "graph.db")) as saver:
        service = BugDiagnosisGraphService(
            checkpointer=saver,
            log_client=_LogClient(),
            code_retriever=retriever,
            entity_memory_repository=entities,
        )
        await service.diagnose(
            "开发环境 traceId: trace-entity-hint-123456 管理员转办报错",
            conversation_id="conversation-entity",
            run_id="run-entity",
            user_id="user-1",
        )

    assert entities.calls[0][1]["owner_id"] == "user-1"
    assert "approval-service" in retriever.states[0]["entity_hints"]
    assert "/transfer" in retriever.states[0]["entity_hints"]
