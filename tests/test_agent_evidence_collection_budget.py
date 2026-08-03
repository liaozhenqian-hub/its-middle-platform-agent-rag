import json

import pytest
from agents.tool_context import ToolContext

from knowledge.agent_runtime.context import AgentRunContext
from knowledge.agent_runtime.rag_tools import (
    create_domain_evidence_tool,
    create_scoped_rag_tool,
)
from knowledge.schemas.documents import FinalSearchResult, MultiRouteSearchResult


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


@pytest.mark.asyncio
async def test_api_contract_supplemental_search_uses_shared_four_call_budget():
    class ContractPipeline:
        def __init__(self):
            self.calls = []

        def search(self, query, *_args):
            self.calls.append(query)
            results = []
            if len(self.calls) == 1:
                results = [
                    FinalSearchResult(
                        rank=1,
                        chunk_id="controller",
                        heading="Controller",
                        content="OrderRequest is the request type.",
                        metadata={"source_type": "code"},
                        retrieval_routes=("keyword",),
                        keyword_score=1.0,
                        vector_distance=None,
                        fusion_score=0.03,
                    )
                ]
            return MultiRouteSearchResult(
                query=query,
                keyword_results=[],
                vector_results=[],
                final_results=results,
            )

    class Repository:
        def get_chunks(self, *_args):
            return []

    class ContractRegistry:
        def __init__(self):
            self.pipeline = ContractPipeline()
            self.repository = Repository()

        def get(self, *_args):
            return self.pipeline

    registry = ContractRegistry()
    composite = create_domain_evidence_tool(
        registry=registry,
        inspector=None,
        source_provider=None,
        app_id="middle-platform",
        domain_id="approval-flow",
        domain_name="Approval flow",
        agent_name="Approval expert",
        max_calls=4,
    )
    scoped = create_scoped_rag_tool(
        registry=registry,
        tool_name="search_domain_code",
        app_id="middle-platform",
        domain_id="approval-flow",
        domain_name="Approval flow",
        source_type="code",
        agent_name="Approval expert",
        max_calls=4,
    )
    context = AgentRunContext(
        "conversation",
        "run",
        task_type="api_contract",
        current_user_message="develop API contract",
    )

    composite_context = ToolContext(
        context=context,
        tool_name=composite.name,
        tool_call_id="composite",
        tool_arguments=json.dumps({"query": "order API"}),
    )
    payload = json.loads(
        await composite.on_invoke_tool(
            composite_context, json.dumps({"query": "order API"})
        )
    )
    scoped_context = ToolContext(
        context=context,
        tool_name=scoped.name,
        tool_call_id="scoped",
        tool_arguments=json.dumps({"query": "order API"}),
    )
    duplicate = json.loads(
        await scoped.on_invoke_tool(scoped_context, json.dumps({"query": "order API"}))
    )

    assert payload["evidence"][0].get("error") is None, payload
    assert payload["executed_retrievals"] == ["code", "swagger"]
    assert len(registry.pipeline.calls) == 2
    assert context.retrieval_call_count == 4
    assert duplicate["status"] == "duplicate_query"
