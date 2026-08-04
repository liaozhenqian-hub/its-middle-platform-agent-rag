import json
import time

import pytest
from agents.tool_context import ToolContext

from knowledge.agent_runtime.context import AgentRunContext
from knowledge.agent_runtime.rag_tools import create_domain_evidence_tool


class SlowPipeline:
    def search(self, *args, **kwargs):
        time.sleep(0.2)
        raise AssertionError("background retrieval should not define the response")


class Registry:
    def get(self, app_id, domain_id):
        return SlowPipeline()


@pytest.mark.asyncio
async def test_controlled_evidence_returns_bounded_timeout_result():
    tool = create_domain_evidence_tool(
        registry=Registry(),
        inspector=None,
        source_provider=None,
        app_id="middle-platform",
        domain_id="workflow",
        domain_name="工作流",
        agent_name="工作流专家",
        retrieval_timeout_seconds=0.01,
    )
    context = AgentRunContext(
        "conversation-timeout",
        "run-timeout",
        task_type="how_to",
        current_user_message="工作流 HTTP 节点怎么配置",
    )
    tool_context = ToolContext(
        context=context,
        tool_name=tool.name,
        tool_call_id="collect-timeout",
        tool_arguments=json.dumps({"query": "HTTP 节点配置"}, ensure_ascii=False),
    )

    payload = json.loads(
        await tool.on_invoke_tool(
            tool_context,
            json.dumps({"query": "HTTP 节点配置"}, ensure_ascii=False),
        )
    )

    assert payload["evidence"][0]["error"] == "retrieval_timeout"
    assert payload["evidence"][0]["timeout_seconds"] == 0.01
