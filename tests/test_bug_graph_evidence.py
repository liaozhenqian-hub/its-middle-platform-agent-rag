from types import SimpleNamespace

import pytest

from knowledge.bug_graph.evidence import ContractEvidenceProvider
from knowledge.schemas.documents import FinalSearchResult, MultiRouteSearchResult


class FakePipeline:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def search(self, *args):
        self.calls.append(args)
        return self.result


class FakeRegistry:
    def __init__(self, pipeline):
        self.pipeline = pipeline

    def get(self, app_id, domain):
        assert (app_id, domain) == ("middle-platform", None)
        return self.pipeline


class FakeSwaggerProvider:
    def __init__(self):
        self.calls = []

    async def list_for_domain(self, domain_id):
        self.calls.append(domain_id)
        return [SimpleNamespace(source_id="swagger-workflow")]


class FakeSwaggerInspector:
    async def inspect(self, source, query, top_k=5):
        return {
            "refreshed_at": "2026-07-16T08:00:00+00:00",
            "stale": False,
            "operations": [
                {
                    "operation_id": "createOrder",
                    "method": "POST",
                    "path": "/orders",
                    "summary": "Create an order",
                }
            ],
        }


def document_result():
    item = FinalSearchResult(
        rank=1,
        chunk_id="doc-1",
        heading="订单创建约束",
        content="contract excerpt",
        metadata={
            "source_type": "product_document",
            "domain_id": "workflow",
            "relative_path": "docs/orders.md",
        },
        retrieval_routes=("keyword", "vector"),
        keyword_score=1.0,
        vector_distance=0.1,
        fusion_score=0.03,
        rerank_score=0.8,
    )
    return MultiRouteSearchResult(
        query="POST /orders",
        keyword_results=[],
        vector_results=[],
        final_results=[item],
        rerank_applied=True,
    )


@pytest.mark.asyncio
async def test_contract_evidence_requires_observed_endpoint_and_code_domain():
    pipeline = FakePipeline(document_result())
    swagger_provider = FakeSwaggerProvider()
    provider = ContractEvidenceProvider(
        registry=FakeRegistry(pipeline),
        swagger_inspector=FakeSwaggerInspector(),
        swagger_source_provider=swagger_provider,
    )
    code = [{"metadata": {"domain_id": "workflow"}}]

    missing_endpoint = await provider.enrich(
        {"normalized_problem": "create order", "log_endpoints": []}, code
    )
    missing_domain = await provider.enrich(
        {"normalized_problem": "create order", "log_endpoints": ["/orders"]},
        [{"metadata": {"domain_id": "shared"}}],
    )

    assert missing_endpoint == {"swagger_operations": [], "document_matches": []}
    assert missing_domain == {"swagger_operations": [], "document_matches": []}
    assert pipeline.calls == []
    assert swagger_provider.calls == []


@pytest.mark.asyncio
async def test_contract_evidence_queries_fixed_domain_and_registered_swagger_sources():
    pipeline = FakePipeline(document_result())
    swagger_provider = FakeSwaggerProvider()
    provider = ContractEvidenceProvider(
        registry=FakeRegistry(pipeline),
        swagger_inspector=FakeSwaggerInspector(),
        swagger_source_provider=swagger_provider,
        top_k=3,
    )

    result = await provider.enrich(
        {
            "normalized_problem": "create order failed",
            "log_endpoints": ["/orders"],
        },
        [{"metadata": {"domain_id": "workflow"}}],
    )

    assert swagger_provider.calls == ["workflow"]
    assert pipeline.calls[0][4] == {
        "$and": [
            {"$or": [{"domain_id": "workflow"}, {"domain_id": "shared"}]},
            {"source_type": "product_document"},
        ]
    }
    assert result["document_matches"][0]["chunk_id"] == "doc-1"
    assert result["swagger_operations"][0]["source_id"] == "swagger-workflow"
