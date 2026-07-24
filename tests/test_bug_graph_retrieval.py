from types import SimpleNamespace

import pytest

from knowledge.bug_graph.retrieval import PipelineBugCodeRetriever
from knowledge.logs.grafana import TraceLogResult
from knowledge.schemas.documents import FinalSearchResult, KnowledgeChunk, MultiRouteSearchResult


class FakeRepository:
    def __init__(self, exact_chunks=None, contextual_chunks=None):
        self.exact_chunks = exact_chunks or []
        self.contextual_chunks = contextual_chunks or []
        self.calls = []

    def get_chunks(self, where=None, ids=None):
        self.calls.append((where, ids))
        if where and any(
            clause.get("symbol_name") == "OrderService.create"
            for clause in where.get("$and", [])
        ):
            return self.exact_chunks
        if where and any(
            clause.get("relative_path") == "service/OrderService.java"
            for clause in where.get("$and", [])
        ):
            return self.contextual_chunks
        return []


class FakePipeline:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def search(self, *args):
        self.calls.append(args)
        return self.result


class FakeRegistry:
    def __init__(self, repository, pipeline):
        self.repository = repository
        self.pipeline = pipeline

    def get(self, app_id, domain):
        assert (app_id, domain) == ("middle-platform", None)
        return self.pipeline


def state():
    return {
        "environment": "test",
        "normalized_problem": "创建订单空指针",
        "exception_types": ["NullPointerException"],
        "stack_frames": [
            {
                "symbol": "com.example.OrderService.create",
                "file": "OrderService.java",
                "line": 156,
            }
        ],
    }


def logs():
    return TraceLogResult(
        trace_id="trace-123456",
        environment="test",
        code_branch="develop",
        from_ms=1000,
        to_ms=2000,
        entries=(),
        exception_types=("NullPointerException",),
    )


@pytest.mark.asyncio
async def test_bug_code_retriever_prefers_exact_symbol_metadata_match():
    exact = KnowledgeChunk(
        chunk_id="exact-1",
        heading="OrderService.create",
        content="exact code",
        metadata={
            "source_type": "code",
            "branch": "develop",
            "domain_id": "workflow",
            "symbol_name": "OrderService.create",
        },
    )
    repository = FakeRepository([exact])
    pipeline = FakePipeline(SimpleNamespace())
    retriever = PipelineBugCodeRetriever(
        FakeRegistry(repository, pipeline),
        top_k=5,
        min_rerank_score=0.35,
    )

    results = await retriever.search(state(), logs())

    assert [item["chunk_id"] for item in results] == ["exact-1"]
    assert results[0]["match_type"] == "exact_symbol"
    assert pipeline.calls == []


@pytest.mark.asyncio
async def test_bug_code_retriever_accepts_only_hybrid_results_above_threshold():
    accepted = FinalSearchResult(
        rank=1,
        chunk_id="accepted",
        heading="OrderService.create",
        content="accepted code",
        metadata={"source_type": "code", "branch": "develop"},
        retrieval_routes=("keyword", "vector"),
        keyword_score=1.0,
        vector_distance=0.1,
        fusion_score=0.03,
        rerank_score=0.72,
    )
    rejected = FinalSearchResult(
        rank=2,
        chunk_id="rejected",
        heading="OtherService",
        content="nearest neighbor",
        metadata={"source_type": "code", "branch": "develop"},
        retrieval_routes=("vector",),
        keyword_score=None,
        vector_distance=0.2,
        fusion_score=0.01,
        rerank_score=0.2,
    )
    pipeline_result = MultiRouteSearchResult(
        query="创建订单空指针",
        keyword_results=[],
        vector_results=[],
        final_results=[accepted, rejected],
        rerank_applied=True,
    )
    repository = FakeRepository()
    pipeline = FakePipeline(pipeline_result)
    retriever = PipelineBugCodeRetriever(
        FakeRegistry(repository, pipeline),
        top_k=5,
        min_rerank_score=0.35,
    )

    results = await retriever.search(state(), logs())

    assert [item["chunk_id"] for item in results] == ["accepted"]
    assert pipeline.calls[0][4] == {
        "$and": [{"source_type": "code"}, {"branch": "develop"}]
    }


@pytest.mark.asyncio
async def test_bug_code_retriever_rejects_hybrid_results_when_rerank_falls_back():
    fallback = MultiRouteSearchResult(
        query="创建订单空指针",
        keyword_results=[],
        vector_results=[],
        final_results=[],
        rerank_applied=False,
    )
    retriever = PipelineBugCodeRetriever(
        FakeRegistry(FakeRepository(), FakePipeline(fallback)),
        top_k=5,
        min_rerank_score=0.35,
    )

    assert await retriever.search(state(), logs()) == []


@pytest.mark.asyncio
async def test_bug_code_retriever_enriches_method_with_containing_type_context():
    container = KnowledgeChunk(
        chunk_id="class-1",
        heading="OrderService",
        content="class OrderService implements OrderApi",
        metadata={
            "source_type": "code",
            "branch": "develop",
            "relative_path": "service/OrderService.java",
            "symbol_type": "class",
            "symbol_name": "OrderService",
            "imports": "import java.util.Objects;",
            "implements": '["OrderApi"]',
        },
    )
    repository = FakeRepository(contextual_chunks=[container])
    retriever = PipelineBugCodeRetriever(
        FakeRegistry(repository, FakePipeline(SimpleNamespace())),
    )
    matches = [
        {
            "chunk_id": "method-1",
            "heading": "OrderService.create",
            "content": "void create() {}",
            "domain": "workflow",
            "metadata": {
                "source_type": "code",
                "branch": "develop",
                "relative_path": "service/OrderService.java",
                "symbol_name": "OrderService.create",
            },
        }
    ]

    enriched = await retriever.enrich(state(), matches)

    assert enriched[0]["context_chunks"][0]["chunk_id"] == "class-1"
    assert enriched[0]["context_chunks"][0]["content"].startswith("class OrderService")
    assert enriched[0]["structural_context"]["implements"] == '["OrderApi"]'
