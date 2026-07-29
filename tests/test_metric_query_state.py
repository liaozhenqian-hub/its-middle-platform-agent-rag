import asyncio
from types import SimpleNamespace

import pytest

from knowledge.agent_runtime.metric_gateway import MetricQueryGuard
from knowledge.agent_runtime.metric_mcp import MetricMCPResultCache
from knowledge.agent_runtime.context import AgentRunContext


class FakeServer:
    def __init__(self):
        self.calls = []

    async def call_tool(self, name, arguments, meta=None):
        self.calls.append((name, arguments))
        return SimpleNamespace(
            isError=False,
            content=[SimpleNamespace(type="text", text='{"rows":[1]}')],
        )


def test_metric_cache_normalizes_arguments_and_keeps_scope_keys_separate():
    cache = MetricMCPResultCache(ttl_seconds=60, max_entries=10)
    cache.put("user-a", "conversation-a", "searchBizMetric", {"q": "每日 2C"}, {"items": [1]})

    assert cache.get("user-a", "conversation-a", "searchBizMetric", {"q": " 每日2C "}) == {"items": [1]}
    assert cache.get("user-b", "conversation-a", "searchBizMetric", {"q": "每日2C"}) is None
    assert cache.get("user-a", "conversation-b", "searchBizMetric", {"q": "每日2C"}) is None


@pytest.mark.asyncio
async def test_metric_guard_reuses_confirmed_query_result_without_second_mcp_call():
    server = FakeServer()
    guard = MetricQueryGuard(server)
    context = AgentRunContext(
        "conversation-a",
        "run-a",
        user_id="user-a",
        current_user_message="确认使用指标应用",
    )
    prepared = guard.prepare(context, selected_app="指标应用")

    first = await guard.query_data(
        context,
        req={"appId": 1, "page": {"size": 10}},
        limit=10,
        confirmation_token=prepared["confirmation_token"],
    )
    second = await guard.query_data(
        context,
        req={"appId": 1, "page": {"size": 10}},
        limit=10,
        confirmation_token=prepared["confirmation_token"],
    )

    assert first["status"] == second["status"] == "completed"
    assert second["cache_hit"] is True
    assert len(server.calls) == 1
