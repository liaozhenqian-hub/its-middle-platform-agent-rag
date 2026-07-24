import json

import pytest
from agents.tool_context import ToolContext

from knowledge.agent_runtime.context import AgentRunContext
from knowledge.agent_runtime.swagger_tools import create_domain_swagger_tool
from knowledge.swagger.inspector import SwaggerSource


class FakeProvider:
    async def list_for_domain(self, domain_id):
        assert domain_id == "metric-platform"
        return [
            SwaggerSource(
                source_id="swagger-1",
                url="https://swagger.internal/openapi.json",
                auth_type="none",
            )
        ]


class FakeInspector:
    async def inspect(self, source, query):
        assert query == "指标详情接口"
        return {
            "source_id": source.source_id,
            "stale": False,
            "refreshed_at": "2026-07-15T00:00:00+00:00",
            "operations": [
                {
                    "operation_id": "getMetric",
                    "method": "GET",
                    "path": "/api/metrics/{id}",
                    "summary": "查询指标详情",
                }
            ],
        }


class FailingInspector:
    async def inspect(self, source, query):
        raise RuntimeError(
            "failed https://swagger.internal/private.json secret-token"
        )


@pytest.mark.asyncio
async def test_domain_swagger_tool_collects_typed_citation_and_hides_source_config():
    tool = create_domain_swagger_tool(
        inspector=FakeInspector(),
        source_provider=FakeProvider(),
        domain_id="metric-platform",
        domain_name="指标平台",
        agent_name="指标平台专家",
    )
    context = AgentRunContext("conversation-1", "run-1", domain_id="metric-platform")
    tool_context = ToolContext(
        context=context,
        tool_name=tool.name,
        tool_call_id="call-1",
        tool_arguments='{"query":"指标详情接口"}',
    )

    output = await tool.on_invoke_tool(tool_context, '{"query":"指标详情接口"}')
    payload = json.loads(output)

    assert payload["sources"][0]["operations"][0]["operation_id"] == "getMetric"
    assert "swagger.internal" not in output
    assert context.citations[0].source_type == "swagger"
    assert context.citations[0].source_id == "swagger-1:getMetric"
    assert context.tool_runs[0].status == "completed"


@pytest.mark.asyncio
async def test_domain_swagger_tool_sanitizes_inspector_failures():
    tool = create_domain_swagger_tool(
        inspector=FailingInspector(),
        source_provider=FakeProvider(),
        domain_id="metric-platform",
        domain_name="指标平台",
        agent_name="指标平台专家",
    )
    context = AgentRunContext("conversation-1", "run-1", domain_id="metric-platform")
    tool_context = ToolContext(
        context=context,
        tool_name=tool.name,
        tool_call_id="call-2",
        tool_arguments='{"query":"指标详情接口"}',
    )

    output = await tool.on_invoke_tool(tool_context, '{"query":"指标详情接口"}')

    assert "Swagger 定义暂时不可用" in output
    assert "swagger.internal" not in output
    assert "secret-token" not in output
    assert context.tool_runs[0].status == "failed"
