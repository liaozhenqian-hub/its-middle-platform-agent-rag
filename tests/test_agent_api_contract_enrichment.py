import json

import pytest
from agents.tool_context import ToolContext

from knowledge.agent_runtime.context import AgentRunContext
from knowledge.agent_runtime.rag_tools import (
    _referenced_contract_types,
    create_domain_evidence_tool,
)
from knowledge.schemas.documents import FinalSearchResult, KnowledgeChunk, MultiRouteSearchResult


class ApiPipeline:
    def __init__(self):
        self.queries = []

    def search(self, query, keyword_k, vector_k, final_k, where):
        self.queries.append(query)
        if len(self.queries) == 1:
            heading = "MetricClient.getDataV2"
            content = "CubeLoadV2RespVO getDataV2(MetricReqVO reqVO)"
            chunk_id = "method"
        else:
            heading = "MetricReqVO"
            content = "class MetricReqVO { Integer limit; Integer offset; Boolean total; }"
            chunk_id = "request-fields"
        hit = FinalSearchResult(
            rank=1,
            chunk_id=chunk_id,
            heading=heading,
            content=content,
            metadata={"source_type": "code", "symbol_type": "class"},
            retrieval_routes=("keyword",),
            keyword_score=1.0,
            vector_distance=None,
            fusion_score=1.0,
        )
        return MultiRouteSearchResult(
            query=query,
            keyword_results=[],
            vector_results=[],
            final_results=[hit],
        )


class Registry:
    def __init__(self, pipeline):
        self.pipeline = pipeline
        self.repository = self
        self.chunk_queries = []

    def get(self, app_id, domain_id):
        return self.pipeline

    def get_chunks(self, where, ids=None):
        self.chunk_queries.append(where)
        clauses = where.get("$and", [])
        symbol_clause = clauses[-1] if clauses else None
        if symbol_clause != {
            "$or": [
                {"symbol_name": "MetricReqVO"},
                {"symbol_name": "CubeLoadV2RespVO"},
            ]
        }:
            return []
        return [
            KnowledgeChunk(
                chunk_id="request-fields",
                heading="MetricReqVO",
                content="class MetricReqVO { Integer limit; Integer offset; Boolean total; }",
                metadata={
                    "source_type": "code",
                    "symbol_type": "class",
                    "symbol_name": "MetricReqVO",
                },
            )
        ]


@pytest.mark.asyncio
async def test_api_contract_enriches_referenced_request_type_fields():
    pipeline = ApiPipeline()
    registry = Registry(pipeline)
    tool = create_domain_evidence_tool(
        registry=registry,
        inspector=None,
        source_provider=None,
        app_id="middle-platform",
        domain_id="metric-platform",
        domain_name="指标平台",
        agent_name="指标平台专家",
    )
    context = AgentRunContext(
        "conversation",
        "run",
        task_type="api_contract",
        current_user_message="getDataV2 入参和分页字段",
    )
    tool_context = ToolContext(
        context=context,
        tool_name=tool.name,
        tool_call_id="collect",
        tool_arguments=json.dumps({"query": "getDataV2 入参和分页字段"}, ensure_ascii=False),
    )

    payload = json.loads(
        await tool.on_invoke_tool(
            tool_context,
            json.dumps({"query": "getDataV2 入参和分页字段"}, ensure_ascii=False),
        )
    )

    code_results = payload["evidence"][0]["results"]
    assert len(registry.chunk_queries) == 1
    assert registry.chunk_queries[0]["$and"][-1] == {
        "$or": [
            {"symbol_name": "MetricReqVO"},
            {"symbol_name": "CubeLoadV2RespVO"},
        ]
    }
    assert [item["chunk_id"] for item in code_results] == ["request-fields", "method"]
    assert pipeline.queries == [pipeline.queries[0]]


def test_api_contract_prioritizes_business_dtos_over_generic_api_wrappers():
    hit = FinalSearchResult(
        rank=1,
        chunk_id="instance-detail",
        heading="ProcessInstanceApiController.getInstanceDetail",
        content=(
            "ApiResponseVO<ProcessInstanceDetailRespVO> "
            "getInstanceDetail(ApiRequestVO<ProcessInstanceDetailReqVO> request)"
        ),
        metadata={"source_type": "code", "symbol_type": "method"},
        retrieval_routes=("keyword",),
        keyword_score=1.0,
        vector_distance=None,
        fusion_score=1.0,
    )
    result = MultiRouteSearchResult(
        query="getInstanceDetail",
        keyword_results=[],
        vector_results=[],
        final_results=[hit],
    )

    referenced = _referenced_contract_types(result, "getInstanceDetail")

    assert referenced[:2] == [
        "ProcessInstanceDetailReqVO",
        "ProcessInstanceDetailRespVO",
    ]


@pytest.mark.asyncio
async def test_api_contract_prioritizes_types_near_the_matched_method():
    class MethodPipeline:
        def __init__(self):
            self.final_k = None

        def search(self, query, *_args):
            self.final_k = max(self.final_k or 0, _args[2])
            broad_hit = FinalSearchResult(
                rank=1,
                chunk_id="controller-class",
                heading="ProcessTaskController",
                content="AdminTransferNodeVO node; AdminTransferUserVO user;",
                metadata={"source_type": "code", "symbol_type": "class"},
                retrieval_routes=("keyword",),
                keyword_score=1.0,
                vector_distance=None,
                fusion_score=1.0,
            )
            method_hit = FinalSearchResult(
                rank=1,
                chunk_id="controller-method",
                heading="ProcessTaskController.adminTransferTask",
                content=(
                    "LegacyReqVO legacy; UnrelatedRespVO unrelated; "
                    "AuditRequest audit; "
                    "adminTransferTask(AdminTransferReqVO reqVO)"
                ),
                metadata={"source_type": "code", "symbol_type": "method"},
                retrieval_routes=("keyword",),
                keyword_score=1.0,
                vector_distance=None,
                fusion_score=1.0,
            )
            return MultiRouteSearchResult(
                query=query,
                keyword_results=[],
                vector_results=[],
                    final_results=[broad_hit, method_hit],
            )

    class MethodRegistry:
        def __init__(self):
            self.pipeline = MethodPipeline()
            self.repository = self
            self.requested_symbols = []

        def get(self, *_args):
            return self.pipeline

        def get_chunks(self, where, ids=None):
            symbol_clause = where["$and"][-1]
            self.requested_symbols = [
                item["symbol_name"] for item in symbol_clause["$or"]
            ]
            return []

    registry = MethodRegistry()
    tool = create_domain_evidence_tool(
        registry=registry,
        inspector=None,
        source_provider=None,
        app_id="middle-platform",
        domain_id="approval-flow",
        domain_name="Approval flow",
        agent_name="Approval expert",
    )
    context = AgentRunContext(
        "conversation",
        "run",
        task_type="api_contract",
        current_user_message="审批流管理员转办接口的 URL、入参和出参是什么？",
    )
    tool_context = ToolContext(
        context=context,
        tool_name=tool.name,
        tool_call_id="collect",
        tool_arguments=json.dumps({"query": context.current_user_message}, ensure_ascii=False),
    )

    await tool.on_invoke_tool(
        tool_context,
        json.dumps({"query": context.current_user_message}, ensure_ascii=False),
    )

    assert registry.requested_symbols[0] == "AdminTransferReqVO"
    assert registry.pipeline.final_k >= 8


@pytest.mark.asyncio
async def test_api_contract_deduplicates_identical_exact_types_across_branches():
    pipeline = ApiPipeline()

    class DuplicateRegistry(Registry):
        def get_chunks(self, where, ids=None):
            return [
                KnowledgeChunk(
                    chunk_id=f"request-{branch}",
                    heading="MetricReqVO",
                    content="class MetricReqVO { Integer limit; }",
                    metadata={
                        "source_type": "code",
                        "symbol_name": "MetricReqVO",
                        "branch": branch,
                    },
                )
                for branch in ("develop", "master")
            ]

    registry = DuplicateRegistry(pipeline)
    tool = create_domain_evidence_tool(
        registry=registry,
        inspector=None,
        source_provider=None,
        app_id="middle-platform",
        domain_id="metric-platform",
        domain_name="Metric platform",
        agent_name="Metric expert",
    )
    context = AgentRunContext(
        "conversation",
        "run",
        task_type="api_contract",
        current_user_message="getDataV2 入参字段是什么？",
    )
    tool_context = ToolContext(
        context=context,
        tool_name=tool.name,
        tool_call_id="collect",
        tool_arguments=json.dumps({"query": context.current_user_message}, ensure_ascii=False),
    )

    payload = json.loads(
        await tool.on_invoke_tool(
            tool_context,
            json.dumps({"query": context.current_user_message}, ensure_ascii=False),
        )
    )

    exact_results = [
        item
        for item in payload["evidence"][0]["results"]
        if item["retrieval_routes"] == ["exact_symbol"]
    ]
    assert [item["heading"] for item in exact_results] == ["MetricReqVO"]
