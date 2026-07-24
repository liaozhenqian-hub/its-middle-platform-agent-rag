import json
from types import SimpleNamespace

import pytest

from knowledge.agent_runtime.context import AgentRunContext
from knowledge.agent_runtime.metric_gateway import MetricQueryGuard


class FakeMCPServer:
    def __init__(self):
        self.calls = []

    async def call_tool(self, name, arguments, meta=None):
        self.calls.append((name, arguments))
        return SimpleNamespace(
            isError=False,
            content=[SimpleNamespace(type="text", text=json.dumps({"records": [1]}))],
        )


def test_metric_guard_requires_explicit_user_application_confirmation():
    guard = MetricQueryGuard(FakeMCPServer())
    context = AgentRunContext(
        "conversation-1",
        "run-1",
        current_user_message="帮我查近七天2C包裹数，应用不确定就先问我",
    )

    result = guard.prepare(context, selected_app="海外仓全局经营分析")

    assert result["status"] == "clarification_required"
    assert context.response_mode == "clarification"
    assert context.metric_confirmation_token is None


@pytest.mark.asyncio
async def test_metric_guard_allows_data_only_after_confirmed_application():
    server = FakeMCPServer()
    guard = MetricQueryGuard(server)
    context = AgentRunContext(
        "conversation-1",
        "run-1",
        current_user_message="确认使用海外仓全局经营分析",
    )
    prepared = guard.prepare(context, selected_app="海外仓全局经营分析")

    result = await guard.query_data(
        context,
        req={"appId": 123, "page": {"current": 1, "size": 10}},
        limit=10,
        confirmation_token=prepared["confirmation_token"],
    )

    assert result["status"] == "completed"
    assert server.calls == [
        (
            "searchMetricAppQueryResult",
            {"req": {"appId": 123, "page": {"current": 1, "size": 10}}, "limit": 10},
        )
    ]


@pytest.mark.asyncio
async def test_metric_guard_rejects_invalid_token_without_calling_mcp():
    server = FakeMCPServer()
    guard = MetricQueryGuard(server)
    context = AgentRunContext("conversation-1", "run-1")

    result = await guard.query_sql(
        context,
        metric_type="derived",
        name="销售额",
        confirmation_token="invalid",
    )

    assert result["status"] == "clarification_required"
    assert server.calls == []
