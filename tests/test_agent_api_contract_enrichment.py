import json

import pytest
from agents.tool_context import ToolContext

from knowledge.agent_runtime.context import AgentRunContext
from knowledge.agent_runtime.rag_tools import create_domain_evidence_tool
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
                {"symbol_name": "CubeLoadV2RespVO"},
                {"symbol_name": "MetricReqVO"},
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
            {"symbol_name": "CubeLoadV2RespVO"},
            {"symbol_name": "MetricReqVO"},
        ]
    }
    assert [item["chunk_id"] for item in code_results] == ["request-fields", "method"]
    assert pipeline.queries == [pipeline.queries[0]]
