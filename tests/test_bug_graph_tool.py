import json

import pytest
from agents.tool_context import ToolContext

from knowledge.agent_runtime.context import AgentRunContext
from knowledge.bug_graph.service import BugDiagnosisResult
from knowledge.bug_graph.tool import create_bug_graph_tool


class FakeGraphService:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def diagnose(self, bug_report, *, conversation_id, run_id):
        self.calls.append((bug_report, conversation_id, run_id))
        return self.result


@pytest.mark.asyncio
async def test_bug_graph_tool_marks_clarification_without_citation():
    service = FakeGraphService(
        BugDiagnosisResult(
            status="clarification_required",
            answer="请补充 trace ID。",
            missing_fields=["trace_id"],
            citations=[],
        )
    )
    tool = create_bug_graph_tool(service)
    context = AgentRunContext("conversation-1", "run-1")
    tool_context = ToolContext(
        context=context,
        tool_name=tool.name,
        tool_call_id="call-1",
        tool_arguments='{"bug_report":"开发环境接口报错"}',
    )

    output = json.loads(
        await tool.on_invoke_tool(
            tool_context,
            '{"bug_report":"开发环境接口报错"}',
        )
    )

    assert output["status"] == "clarification_required"
    assert context.response_mode == "clarification"
    assert context.response_override == "请补充 trace ID。"
    assert context.citations == []
    assert service.calls == [("开发环境接口报错", "conversation-1", "run-1")]


@pytest.mark.asyncio
async def test_bug_graph_tool_prefers_raw_current_user_message_over_manager_rewrite():
    service = FakeGraphService(
        BugDiagnosisResult(
            status="clarification_required",
            answer="请补充 trace ID。",
            missing_fields=["trace_id"],
            citations=[],
        )
    )
    tool = create_bug_graph_tool(service)
    context = AgentRunContext(
        "conversation-raw",
        "run-raw",
        current_user_message="开发环境，traceId: trace-raw-123456",
    )
    tool_context = ToolContext(
        context=context,
        tool_name=tool.name,
        tool_call_id="call-raw",
        tool_arguments='{"bug_report":"请确认开发、测试或生产环境"}',
    )

    await tool.on_invoke_tool(
        tool_context,
        '{"bug_report":"请确认开发、测试或生产环境"}',
    )

    assert service.calls == [
        (
            "开发环境，traceId: trace-raw-123456",
            "conversation-raw",
            "run-raw",
        )
    ]


@pytest.mark.asyncio
async def test_bug_graph_tool_collects_typed_citations_for_completed_result():
    service = FakeGraphService(
        BugDiagnosisResult(
            status="completed",
            answer="诊断完成",
            missing_fields=[],
            evidence_grade="correlated",
            citations=[
                {
                    "source_type": "code",
                    "source_id": "code-1",
                    "title": "OrderService.create",
                    "domain": "workflow",
                    "metadata": {"branch": "develop"},
                }
            ],
        )
    )
    tool = create_bug_graph_tool(service)
    context = AgentRunContext("conversation-1", "run-1")
    tool_context = ToolContext(
        context=context,
        tool_name=tool.name,
        tool_call_id="call-1",
        tool_arguments='{"bug_report":"测试环境 trace ID trace-123456"}',
    )

    output = json.loads(
        await tool.on_invoke_tool(
            tool_context,
            '{"bug_report":"测试环境 trace ID trace-123456"}',
        )
    )

    assert output["evidence_grade"] == "correlated"
    assert context.response_mode == "answer"
    assert context.response_override == "诊断完成"
    assert context.citations[0].source_type == "code"
    assert context.citations[0].source_id == "code-1"
