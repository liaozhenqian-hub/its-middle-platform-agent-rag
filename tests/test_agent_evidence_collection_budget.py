import json

import pytest
from agents.tool_context import ToolContext

from knowledge.agent_runtime.context import AgentRunContext
from knowledge.agent_runtime.rag_tools import create_domain_evidence_tool
from knowledge.schemas.documents import MultiRouteSearchResult


class Pipeline:
    def search(self, query, *args):
        return MultiRouteSearchResult(
            query=query,
            keyword_results=[],
            vector_results=[],
            final_results=[],
        )


class Registry:
    def get(self, app_id, domain_id):
        return Pipeline()


@pytest.mark.asyncio
async def test_composite_evidence_is_collected_once_per_domain_even_for_rephrasing():
    tool = create_domain_evidence_tool(
        registry=Registry(),
        inspector=None,
        source_provider=None,
        app_id="middle-platform",
        domain_id="approval-flow",
        domain_name="审批流",
        agent_name="审批流专家",
    )
    context = AgentRunContext(
        "conversation",
        "run",
        task_type="code_lookup",
        current_user_message="管理员转办 develop 分支代码",
    )

    async def invoke(call_id: str, query: str):
        tool_context = ToolContext(
            context=context,
            tool_name=tool.name,
            tool_call_id=call_id,
            tool_arguments=json.dumps({"query": query}, ensure_ascii=False),
        )
        return json.loads(
            await tool.on_invoke_tool(
                tool_context,
                json.dumps({"query": query}, ensure_ascii=False),
            )
        )

    first = await invoke("one", "管理员转办代码")
    second = await invoke("two", "转办功能实现在哪里")

    assert first["task_type"] == "code_lookup"
    assert second["status"] == "duplicate_query"
    assert second["reuse_existing_evidence"] is True
