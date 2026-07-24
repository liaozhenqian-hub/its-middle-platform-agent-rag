import json

import pytest
from agents.tool_context import ToolContext

from knowledge.agent_runtime.bug_tools import (
    create_bug_code_search_tool,
    create_trace_log_tool,
)
from knowledge.agent_runtime.context import AgentRunContext
from knowledge.logs.grafana import LogEntry, StackFrame, TraceLogResult
from knowledge.schemas.documents import FinalSearchResult, MultiRouteSearchResult


class FakeLogClient:
    def __init__(self):
        self.calls = []

    async def query_trace(self, trace_id, environment, time_range_minutes):
        self.calls.append((trace_id, environment, time_range_minutes))
        return TraceLogResult(
            trace_id=trace_id,
            environment=environment,
            code_branch="develop" if environment != "prod" else "master",
            from_ms=1000,
            to_ms=2000,
            entries=(
                LogEntry(
                    timestamp="1000",
                    level="ERROR",
                    logger="OrderService",
                    message="sanitized failure",
                    exception_types=("NullPointerException",),
                    stack_frames=(
                        StackFrame("OrderService.create", "OrderService.java", 156),
                    ),
                ),
            ),
            exception_types=("NullPointerException",),
        )


class FakePipeline:
    def __init__(self):
        self.calls = []

    def search(self, query, keyword_k, vector_k, final_k, where=None):
        self.calls.append((query, keyword_k, vector_k, final_k, where))
        return MultiRouteSearchResult(
            query=query,
            keyword_results=[],
            vector_results=[],
            final_results=[
                FinalSearchResult(
                    rank=1,
                    chunk_id="code-1",
                    heading="OrderService.create",
                    content="model-facing code excerpt",
                    metadata={
                        "source_type": "code",
                        "branch": "develop",
                        "relative_path": "service/OrderService.java",
                        "start_line": 150,
                    },
                    retrieval_routes=("keyword", "vector"),
                    keyword_score=1.0,
                    vector_distance=0.1,
                    fusion_score=0.03,
                )
            ],
        )


class FakeRegistry:
    def __init__(self, pipeline):
        self.pipeline = pipeline

    def get(self, app_id, domain):
        assert (app_id, domain) == ("middle-platform", None)
        return self.pipeline


@pytest.mark.asyncio
async def test_trace_log_tool_returns_sanitized_evidence_but_public_citation_has_no_lines():
    client = FakeLogClient()
    tool = create_trace_log_tool(client, agent_name="Bug 分析专家")
    context = AgentRunContext(conversation_id="conversation-1", run_id="run-1")
    tool_context = ToolContext(
        context=context,
        tool_name=tool.name,
        tool_call_id="call-log",
        tool_arguments='{"trace_id":"trace-abc-123","environment":"test"}',
    )

    output = await tool.on_invoke_tool(
        tool_context,
        '{"trace_id":"trace-abc-123","environment":"test","time_range_minutes":30}',
    )
    payload = json.loads(output)

    assert client.calls == [("trace-abc-123", "test", 30)]
    assert payload["code_branch"] == "develop"
    assert payload["entries"][0]["message"] == "sanitized failure"
    assert context.citations[0].source_type == "log_trace"
    assert "entries" not in context.citations[0].metadata
    assert "sanitized failure" not in str(context.to_dict())
    assert set(tool.params_json_schema["properties"]) == {
        "trace_id",
        "environment",
        "time_range_minutes",
    }


@pytest.mark.asyncio
async def test_trace_log_tool_suppresses_identical_completed_call_in_same_run():
    client = FakeLogClient()
    tool = create_trace_log_tool(client, agent_name="Bug 分析专家")
    context = AgentRunContext(conversation_id="conversation-1", run_id="run-1")

    for call_id, time_range in (("call-log-1", 30), ("call-log-2", 60)):
        tool_context = ToolContext(
            context=context,
            tool_name=tool.name,
            tool_call_id=call_id,
            tool_arguments='{"trace_id":"trace-abc-123","environment":"test"}',
        )
        output = await tool.on_invoke_tool(
            tool_context,
            json.dumps(
                {
                    "trace_id": "trace-abc-123",
                    "environment": "test",
                    "time_range_minutes": time_range,
                }
            ),
        )

    assert client.calls == [("trace-abc-123", "test", 30)]
    assert json.loads(output)["duplicate_call_suppressed"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("environment", "expected_branch"),
    [("develop", "develop"), ("test", "develop"), ("prod", "master")],
)
async def test_bug_code_search_tool_maps_environment_to_server_owned_branch(
    environment,
    expected_branch,
):
    pipeline = FakePipeline()
    tool = create_bug_code_search_tool(
        FakeRegistry(pipeline),
        app_id="middle-platform",
        agent_name="Bug 分析专家",
    )
    context = AgentRunContext(
        conversation_id="conversation-1",
        run_id="run-1",
        domain_id="approval-flow",
    )
    context.add_log_trace_citation(
        trace_id="trace-with-logs-123456",
        environment=environment,
        from_ms=1000,
        to_ms=2000,
        log_count=1,
        exception_types=["NullPointerException"],
        truncated=False,
        entries=[],
    )
    tool_context = ToolContext(
        context=context,
        tool_name=tool.name,
        tool_call_id="call-code",
        tool_arguments=json.dumps({"query": "OrderService.create", "environment": environment}),
    )

    output = await tool.on_invoke_tool(
        tool_context,
        json.dumps({"query": "OrderService.create", "environment": environment}),
    )
    payload = json.loads(output)

    where = pipeline.calls[0][4]
    assert where == {
        "$and": [
            {"source_type": "code"},
            {"branch": expected_branch},
            {"$or": [{"domain_id": "approval-flow"}, {"domain_id": "shared"}]},
        ]
    }
    assert payload["code_branch"] == expected_branch
    assert payload["results"][0]["content"] == "model-facing code excerpt"
    assert context.citations[-1].source_type == "code"
    assert set(tool.params_json_schema["properties"]) == {"query", "environment"}


@pytest.mark.asyncio
async def test_bug_code_search_requires_positive_log_evidence_first():
    pipeline = FakePipeline()
    tool = create_bug_code_search_tool(
        FakeRegistry(pipeline),
        app_id="middle-platform",
        agent_name="Bug 分析专家",
    )
    context = AgentRunContext(conversation_id="conversation-1", run_id="run-1")
    tool_context = ToolContext(
        context=context,
        tool_name=tool.name,
        tool_call_id="call-code",
        tool_arguments='{"query":"OrderService.create","environment":"test"}',
    )

    output = await tool.on_invoke_tool(
        tool_context,
        '{"query":"OrderService.create","environment":"test"}',
    )

    assert pipeline.calls == []
    assert json.loads(output) == {
        "code_search_skipped": True,
        "reason": "positive_log_evidence_required",
    }


@pytest.mark.asyncio
async def test_bug_code_search_skips_when_trace_query_returned_no_logs():
    pipeline = FakePipeline()
    tool = create_bug_code_search_tool(
        FakeRegistry(pipeline),
        app_id="middle-platform",
        agent_name="Bug 分析专家",
    )
    context = AgentRunContext(conversation_id="conversation-1", run_id="run-1")
    context.add_log_trace_citation(
        trace_id="trace-empty-123456",
        environment="test",
        from_ms=1000,
        to_ms=2000,
        log_count=0,
        exception_types=[],
        truncated=False,
        entries=[],
    )
    tool_context = ToolContext(
        context=context,
        tool_name=tool.name,
        tool_call_id="call-code",
        tool_arguments='{"query":"接口报错定位根因","environment":"test"}',
    )

    output = await tool.on_invoke_tool(
        tool_context,
        '{"query":"接口报错定位根因","environment":"test"}',
    )
    payload = json.loads(output)

    assert pipeline.calls == []
    assert payload == {
        "code_search_skipped": True,
        "reason": "trace_query_returned_no_logs",
    }
    assert [citation.source_type for citation in context.citations] == ["log_trace"]
