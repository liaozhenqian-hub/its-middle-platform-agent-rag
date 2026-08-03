import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from agents.tool_context import ToolContext

from knowledge.agent_runtime.context import AgentRunContext
from knowledge.agent_runtime.rag_tools import (
    _citation_metadata,
    create_domain_evidence_tool,
    create_domain_rag_tool,
    create_scoped_rag_tool,
)
from knowledge.schemas.documents import FinalSearchResult, MultiRouteSearchResult


class FakePipeline:
    def __init__(self):
        self.calls = []

    def search(self, query, keyword_k, vector_k, final_k, where=None):
        self.calls.append(
            {
                "query": query,
                "keyword_k": keyword_k,
                "vector_k": vector_k,
                "final_k": final_k,
                "where": where,
            }
        )
        return MultiRouteSearchResult(
            query=query,
            keyword_results=[],
            vector_results=[],
            final_results=[
                FinalSearchResult(
                    rank=1,
                    chunk_id="chunk-1",
                    heading="指标定义",
                    content="供模型回答使用的知识正文",
                    metadata={"domain": "指标平台", "module": "metric"},
                    retrieval_routes=("keyword", "vector"),
                    keyword_score=1.0,
                    vector_distance=0.1,
                    fusion_score=0.03,
                )
            ],
            stage_timings_ms={
                "query_rewrite": 1.0,
                "keyword_search": 2.0,
                "vector_search": 3.0,
                "rerank": 4.0,
            },
        )


class FakeRegistry:
    def __init__(self, pipeline):
        self.pipeline = pipeline
        self.scopes = []

    def get(self, app_id, domain):
        self.scopes.append((app_id, domain))
        return self.pipeline


class SlowRegistry(FakeRegistry):
    def get(self, app_id, domain):
        time.sleep(0.2)
        return super().get(app_id, domain)


def test_citation_metadata_marks_result_matching_a_query_identifier_as_exact():
    item = SimpleNamespace(
        chunk_id="opaque",
        heading="WorkflowService.execute",
        content="implementation",
        metadata={"source_type": "code"},
        retrieval_routes=("keyword",),
        rerank_score=None,
        fusion_score=0.03,
        rank=1,
    )
    result = SimpleNamespace(
        exact_identifiers=("WorkflowService.execute",),
        rerank_applied=False,
    )

    assert _citation_metadata(item, result)["_retrieval"]["exact"] is True


@pytest.mark.asyncio
async def test_domain_rag_tool_has_fixed_scope_and_collects_private_citations():
    pipeline = FakePipeline()
    registry = FakeRegistry(pipeline)
    tool = create_domain_rag_tool(
        registry=registry,
        tool_name="search_metric_platform_knowledge",
        app_id="middle-platform",
        domain="指标平台",
        agent_name="指标平台专家",
        keyword_k=20,
        vector_k=20,
        final_k=5,
    )
    context = AgentRunContext(conversation_id="conversation-1", run_id="run-1")
    tool_context = ToolContext(
        context=context,
        tool_name=tool.name,
        tool_call_id="call-1",
        tool_arguments='{"query":"指标是什么"}',
    )

    output = await tool.on_invoke_tool(tool_context, '{"query":"指标是什么"}')
    payload = json.loads(output)

    assert registry.scopes == [("middle-platform", "指标平台")]
    assert pipeline.calls == [
        {
            "query": "指标是什么",
            "keyword_k": 20,
            "vector_k": 20,
            "final_k": 5,
            "where": None,
        }
    ]
    assert payload["results"][0]["content"] == "供模型回答使用的知识正文"
    assert payload["results"][0]["retrieval_routes"] == ["keyword", "vector"]
    assert context.citations[0].source_id == "chunk-1"
    assert context.citations[0].metadata["_retrieval"] == {
        "exact": False,
        "rerank_applied": False,
        "rerank_score": None,
        "fusion_score": 0.03,
        "rank": 1,
    }
    assert "content" not in context.to_dict()["citations"][0]
    assert context.tool_runs[0].status == "completed"


@pytest.mark.asyncio
async def test_pipeline_construction_does_not_block_the_event_loop():
    tool = create_domain_rag_tool(
        registry=SlowRegistry(FakePipeline()),
        tool_name="search_approval_flow_knowledge",
        app_id="middle-platform",
        domain="审批流",
        agent_name="审批流专家",
    )
    context = AgentRunContext("conversation-slow-build", "run-slow-build")
    tool_context = ToolContext(
        context=context,
        tool_name=tool.name,
        tool_call_id="call-slow-build",
        tool_arguments=json.dumps({"query": "审批接口"}, ensure_ascii=False),
    )

    started_at = time.perf_counter()
    invocation = asyncio.create_task(
        tool.on_invoke_tool(
            tool_context,
            json.dumps({"query": "审批接口"}, ensure_ascii=False),
        )
    )
    await asyncio.sleep(0.01)

    assert time.perf_counter() - started_at < 0.1
    await invocation


def test_domain_rag_tool_only_exposes_query_to_the_model():
    tool = create_domain_rag_tool(
        registry=FakeRegistry(FakePipeline()),
        tool_name="search_workflow_knowledge",
        app_id="middle-platform",
        domain="工作流",
        agent_name="工作流专家",
    )

    assert set(tool.params_json_schema["properties"]) == {"query"}


@pytest.mark.asyncio
async def test_scoped_rag_tool_filters_domain_shared_and_source_type():
    pipeline = FakePipeline()
    registry = FakeRegistry(pipeline)
    tool = create_scoped_rag_tool(
        registry=registry,
        tool_name="search_domain_code",
        app_id="middle-platform",
        domain_id="metric-platform",
        domain_name="指标平台",
        source_type="code",
        agent_name="指标平台专家",
    )
    context = AgentRunContext(
        conversation_id="conversation-1",
        run_id="run-1",
        domain_id="metric-platform",
    )
    tool_context = ToolContext(
        context=context,
        tool_name=tool.name,
        tool_call_id="call-code",
        tool_arguments='{"query":"MetricService"}',
    )

    await tool.on_invoke_tool(tool_context, '{"query":"MetricService"}')

    assert registry.scopes == [("middle-platform", None)]
    where = pipeline.calls[0]["where"]
    assert where == {
        "$and": [
            {"$or": [{"domain_id": "metric-platform"}, {"domain_id": "shared"}]},
            {"source_type": "code"},
        ]
    }
    assert set(tool.params_json_schema["properties"]) == {"query"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "expected_branch"),
    [
        ("这个接口有没有发开发环境", "develop"),
        ("测试环境的代码更新了吗", "develop"),
        ("线上环境是否已经包含这个接口", "master"),
        ("生产环境对应哪个实现", "master"),
        ("这个接口怎么调用", None),
    ],
)
async def test_code_rag_tool_derives_branch_from_user_environment(
    message,
    expected_branch,
):
    pipeline = FakePipeline()
    tool = create_scoped_rag_tool(
        registry=FakeRegistry(pipeline),
        tool_name="search_domain_code",
        app_id="middle-platform",
        domain_id="approval-flow",
        domain_name="审批流",
        source_type="code",
        agent_name="审批流专家",
    )
    context = AgentRunContext(
        "conversation-branch",
        "run-branch",
        current_user_message=message,
    )
    tool_context = ToolContext(
        context=context,
        tool_name=tool.name,
        tool_call_id="call-branch",
        tool_arguments=json.dumps({"query": "审批流转交接口"}, ensure_ascii=False),
    )

    await tool.on_invoke_tool(
        tool_context,
        json.dumps({"query": "审批流转交接口"}, ensure_ascii=False),
    )

    clauses = pipeline.calls[0]["where"]["$and"]
    branch_clauses = [item for item in clauses if "branch" in item]
    assert branch_clauses == ([{"branch": expected_branch}] if expected_branch else [])


@pytest.mark.asyncio
async def test_rag_tool_skips_duplicate_query_and_enforces_run_budget():
    pipeline = FakePipeline()
    tool = create_scoped_rag_tool(
        registry=FakeRegistry(pipeline),
        tool_name="search_domain_code",
        app_id="middle-platform",
        domain_id="workflow",
        domain_name="工作流",
        source_type="code",
        agent_name="工作流专家",
        max_calls=1,
        max_identical_queries=1,
    )
    context = AgentRunContext("conversation-1", "run-1")

    async def invoke(call_id, query):
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

    assert "results" in await invoke("call-1", "HTTP 节点重试")
    assert (await invoke("call-2", "http节点 重试"))["status"] == "duplicate_query"
    assert (await invoke("call-3", "默认分支"))["status"] == "budget_exhausted"
    assert len(pipeline.calls) == 1


@pytest.mark.asyncio
async def test_controlled_evidence_tool_uses_task_plan_and_three_call_budget():
    pipeline = FakePipeline()
    tool = create_domain_evidence_tool(
        registry=FakeRegistry(pipeline),
        inspector=None,
        source_provider=None,
        app_id="middle-platform",
        domain_id="approval-flow",
        domain_name="审批流",
        agent_name="审批流专家",
        max_calls=3,
    )
    context = AgentRunContext(
        "conversation-evidence",
        "run-evidence",
        task_type="how_to",
        current_user_message="审批流怎么配置管理员转办",
    )
    tool_context = ToolContext(
        context=context,
        tool_name=tool.name,
        tool_call_id="collect-1",
        tool_arguments=json.dumps({"query": "管理员转办怎么配置"}, ensure_ascii=False),
    )

    payload = json.loads(
        await tool.on_invoke_tool(
            tool_context,
            json.dumps({"query": "管理员转办怎么配置"}, ensure_ascii=False),
        )
    )

    assert tool.name == "collect_domain_evidence"
    assert set(tool.params_json_schema["properties"]) == {"query"}
    assert payload["task_type"] == "how_to"
    assert payload["executed_retrievals"] == ["product_document", "code"]
    assert len(pipeline.calls) == 2
    assert pipeline.calls[0]["where"]["$and"][1] == {"source_type": "product_document"}
    assert pipeline.calls[1]["where"]["$and"][1] == {"source_type": "code"}
    assert context.retrieval_call_count == 2
    assert [span.name for span in context.runtime_spans] == [
        "retrieval.query_rewrite",
        "retrieval.keyword_search",
        "retrieval.vector_search",
        "retrieval.rerank",
        "retrieval.query_rewrite",
        "retrieval.keyword_search",
        "retrieval.vector_search",
        "retrieval.rerank",
    ]
    assert all(span.kind == "tool" for span in context.runtime_spans)
