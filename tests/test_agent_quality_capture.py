from dataclasses import dataclass

from fastapi.testclient import TestClient

from knowledge.agent_runtime.service import AgentRunResponse
from knowledge.api.app import create_app
from knowledge.quality import TurnCompletion, TurnStart


def response(run_id: str) -> AgentRunResponse:
    return AgentRunResponse(
        status="completed",
        conversation_id="conversation-1",
        run_id=run_id,
        answer="公开回答",
        last_agent="Manager Agent",
        citations=[],
        tool_runs=[],
        approvals=[],
        trace_id="trace-1",
    )


@dataclass
class Started:
    id: str = "turn-1"
    feedback_token: str = "feedback-token"


class FakeQualityCapture:
    def __init__(self):
        self.events: list[tuple[str, object]] = []

    async def start(self, value: TurnStart):
        self.events.append(("start", value))
        return Started()

    async def complete(self, run_id: str, value: TurnCompletion):
        self.events.append((run_id, value))


class FakeAgentService:
    def __init__(self, events):
        self.events = events

    async def chat(self, message, conversation_id=None, *, run_id=None, **kwargs):
        self.events.append(("agent", run_id))
        return response(run_id)

    async def prepare_conversation_scope(self, conversation_id=None, **kwargs):
        return conversation_id or "conversation-1"

    async def stream_chat(self, message, conversation_id=None, *, run_id=None, **kwargs):
        self.events.append(("agent", run_id))
        yield {
            "event": "run.started",
            "data": {"conversation_id": "conversation-1", "run_id": run_id},
        }
        yield {"event": "text.delta", "data": {"delta": "公开回答"}}
        yield {"event": "run.completed", "data": response(run_id).to_dict()}


def test_json_chat_captures_before_agent_and_returns_feedback_metadata():
    capture = FakeQualityCapture()
    service = FakeAgentService(capture.events)
    app = create_app(agent_service=service, quality_capture_service=capture)

    result = TestClient(app).post(
        "/api/v1/agent/chat",
        headers={"X-Client-Channel": "web"},
        json={
            "message": "指标是什么",
            "knowledge_space_id": "middle-platform",
            "domain_id": "metric-platform",
        },
    )

    assert result.status_code == 200
    assert result.json()["quality_turn_id"] == "turn-1"
    assert result.json()["feedback_token"] == "feedback-token"
    assert [item[0] for item in capture.events] == ["start", "agent", result.json()["run_id"]]
    started = capture.events[0][1]
    assert started.question == "指标是什么"
    assert started.channel == "web"
    assert started.domain_id == "metric-platform"
    completed = capture.events[2][1]
    assert completed.status == "completed"
    assert completed.answer == "公开回答"


def test_sse_chat_captures_terminal_response_and_exposes_quality_metadata():
    capture = FakeQualityCapture()
    service = FakeAgentService(capture.events)
    app = create_app(agent_service=service, quality_capture_service=capture)

    with TestClient(app).stream(
        "POST",
        "/api/v1/agent/chat/stream",
        headers={"X-Client-Channel": "web"},
        json={"message": "审批流异常"},
    ) as result:
        body = "".join(result.iter_text())

    assert result.status_code == 200
    assert '"quality_turn_id": "turn-1"' in body
    assert '"feedback_token": "feedback-token"' in body
    assert [item[0] for item in capture.events] == ["start", "agent", capture.events[1][1]]
    assert capture.events[2][1].answer == "公开回答"


class ErrorAgentService(FakeAgentService):
    async def chat(self, message, conversation_id=None, *, run_id=None, **kwargs):
        raise RuntimeError("private model output")

    async def stream_chat(self, message, conversation_id=None, *, run_id=None, **kwargs):
        yield {
            "event": "run.started",
            "data": {"conversation_id": "conversation-1", "run_id": run_id},
        }
        yield {
            "event": "run.error",
            "data": {"conversation_id": "conversation-1", "run_id": run_id, "error": "TimeoutError"},
        }


def test_json_and_sse_failures_are_captured_without_storing_exception_message():
    capture = FakeQualityCapture()
    app = create_app(
        agent_service=ErrorAgentService(capture.events),
        quality_capture_service=capture,
    )
    client = TestClient(app, raise_server_exceptions=False)

    result = client.post("/api/v1/agent/chat", json={"message": "失败问题"})
    assert result.status_code == 500
    assert capture.events[-1][1].status == "error"
    assert capture.events[-1][1].error_type == "RuntimeError"
    assert "private model output" not in repr(capture.events[-1][1])

    capture.events.clear()
    with client.stream(
        "POST", "/api/v1/agent/chat/stream", json={"message": "超时问题"}
    ) as stream:
        assert stream.status_code == 200
        list(stream.iter_text())
    assert capture.events[-1][1].status == "error"
    assert capture.events[-1][1].error_type == "TimeoutError"
