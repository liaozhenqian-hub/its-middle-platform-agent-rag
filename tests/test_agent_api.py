import json

from fastapi.testclient import TestClient

from knowledge.agent_runtime.pending_runs import (
    PendingRunConflictError,
    PendingRunNotFoundError,
)
from knowledge.agent_runtime.context import Citation
from knowledge.agent_runtime.conversation_scopes import ConversationScopeConflictError
from knowledge.agent_runtime.service import AgentRunResponse
from knowledge.api.app import create_app


def response(run_id="run-1", citations=None):
    return AgentRunResponse(
        status="completed",
        conversation_id="conversation-1",
        run_id=run_id,
        answer="回答",
        last_agent="Manager Agent",
        citations=citations or [],
        tool_runs=[],
        approvals=[],
        trace_id="trace-1",
    )


class FakeService:
    def __init__(self):
        self.deleted = []

    async def chat(self, message, conversation_id=None, **kwargs):
        assert message == "指标是什么"
        if conversation_id == "scope-conflict":
            raise ConversationScopeConflictError("scope conflict")
        return response()

    async def prepare_conversation_scope(self, conversation_id=None, **kwargs):
        if conversation_id == "scope-conflict":
            raise ConversationScopeConflictError("scope conflict")
        return conversation_id or "conversation-1"

    async def stream_chat(self, message, conversation_id=None, **kwargs):
        yield {
            "event": "run.started",
            "data": {"conversation_id": "conversation-1", "run_id": "run-1"},
        }
        yield {"event": "text.delta", "data": {"delta": "回答"}}
        yield {"event": "run.completed", "data": response().to_dict()}

    async def decide(self, run_id, decisions):
        if run_id == "missing":
            raise PendingRunNotFoundError(run_id)
        if run_id == "completed":
            raise PendingRunConflictError(run_id)
        return response(run_id)

    async def stream_decide(self, run_id, decisions):
        yield {"event": "run.started", "data": {"run_id": run_id}}
        yield {"event": "run.completed", "data": response(run_id).to_dict()}

    async def require_pending(self, run_id):
        if run_id == "missing":
            raise PendingRunNotFoundError(run_id)
        if run_id == "completed":
            raise PendingRunConflictError(run_id)

    async def delete_conversation(self, conversation_id):
        self.deleted.append(conversation_id)


def build_client():
    service = FakeService()
    app = create_app(
        agent_service=service,
        component_status={
            "model": {"status": "available"},
            "sqlite": {"status": "available"},
            "chroma": {"status": "available"},
            "mcp": {"status": "unavailable"},
        },
    )
    return TestClient(app), service


def test_json_chat_contract_and_blank_validation():
    client, _ = build_client()

    result = client.post(
        "/api/v1/agent/chat",
        json={"conversation_id": "conversation-1", "message": "指标是什么"},
    )

    assert result.status_code == 200
    assert result.json()["status"] == "completed"
    assert result.json()["answer"] == "回答"
    assert result.json()["last_agent"] == "Manager Agent"
    assert client.post("/api/v1/agent/chat", json={"message": "   "}).status_code == 422


def test_chat_rejects_unknown_branch_field_instead_of_silently_ignoring_it():
    client, _ = build_client()

    result = client.post(
        "/api/v1/agent/chat",
        json={"message": "指标是什么", "branch": "develop"},
    )

    assert result.status_code == 422
    assert result.json()["detail"][0]["type"] == "extra_forbidden"


def test_sse_chat_preserves_event_names_and_json_payloads():
    client, _ = build_client()

    with client.stream(
        "POST",
        "/api/v1/agent/chat/stream",
        json={"message": "指标是什么"},
    ) as result:
        body = "".join(result.iter_text())

    assert result.status_code == 200
    assert result.headers["content-type"].startswith("text/event-stream")
    assert "event: run.started" in body
    assert "event: text.delta" in body
    assert "event: run.completed" in body
    assert json.dumps({"delta": "回答"}, ensure_ascii=False) in body


def test_decision_error_mapping_and_conversation_delete():
    client, service = build_client()
    body = {"decisions": [{"tool_call_id": "call-1", "decision": "approve"}]}

    assert client.post("/api/v1/agent/runs/missing/decisions", json=body).status_code == 404
    assert client.post("/api/v1/agent/runs/completed/decisions", json=body).status_code == 409
    assert client.post("/api/v1/agent/runs/run-1/decisions", json=body).status_code == 200
    assert (
        client.post("/api/v1/agent/runs/missing/decisions/stream", json=body).status_code
        == 404
    )
    assert client.delete("/api/v1/agent/conversations/conversation-1").status_code == 204
    assert service.deleted == ["conversation-1"]


def test_health_endpoints_report_degraded_optional_mcp():
    client, _ = build_client()

    assert client.get("/health/live").json() == {"status": "live"}
    ready = client.get("/health/ready")

    assert ready.status_code == 200
    assert ready.json()["status"] == "degraded"
    assert ready.json()["components"]["mcp"]["status"] == "unavailable"


def test_readiness_accepts_provider_aware_postgres_and_pgvector_components():
    application = create_app(
        agent_service=FakeService(),
        component_status={
            "model": {"status": "available"},
            "database": {"provider": "postgres", "status": "available"},
            "vector_store": {"provider": "pgvector", "status": "available"},
            "sqlite": {"status": "disabled"},
            "chroma": {"status": "disabled"},
            "mcp": {"status": "available"},
        },
    )

    ready = TestClient(application).get("/health/ready")

    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"


def test_chat_accepts_scope_and_scope_conflicts_are_http_409_before_streaming():
    client, _ = build_client()
    body = {
        "conversation_id": "scope-conflict",
        "message": "指标是什么",
        "knowledge_space_id": "middle-platform",
        "domain_id": "metric-platform",
    }

    assert client.post("/api/v1/agent/chat", json=body).status_code == 409
    assert client.post("/api/v1/agent/chat/stream", json=body).status_code == 409


def test_agent_response_accepts_typed_code_document_and_swagger_citations():
    citations = [
        Citation(
            source_type="code",
            source_id="code-1",
            title="MetricService.query",
            domain="指标平台",
            metadata={"commit_sha": "abc", "relative_path": "MetricService.java"},
        ),
        Citation(
            source_type="product_document",
            source_id="doc-1",
            title="指标口径",
            domain="指标平台",
            metadata={"source_version": "v2"},
        ),
        Citation(
            source_type="swagger",
            source_id="swagger-1:getMetric",
            title="getMetric",
            domain="指标平台",
            metadata={"method": "GET", "path": "/metrics/{id}"},
        ),
        Citation(
            source_type="log_trace",
            source_id="trace-abc-123",
            title="test trace trace-abc-123",
            domain="中台",
            metadata={
                "environment": "test",
                "from_ms": 1000,
                "to_ms": 2000,
                "log_count": 2,
                "exception_types": ["NullPointerException"],
                "truncated": False,
            },
        ),
    ]
    service = FakeService()

    class TypedCitationService(FakeService):
        async def chat(self, message, conversation_id=None, **kwargs):
            return response(citations=citations)

    app = create_app(agent_service=TypedCitationService(), component_status={})
    result = TestClient(app).post(
        "/api/v1/agent/chat",
        json={"message": "指标是什么", "domain_id": "metric-platform"},
    )

    assert result.status_code == 200
    assert [item["source_type"] for item in result.json()["citations"]] == [
        "code",
        "product_document",
        "swagger",
        "log_trace",
    ]
