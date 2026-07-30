from knowledge.schemas.documents import (
    FinalSearchResult,
    HybridRankResult,
    KeywordIndexRecord,
    KnowledgeChunk,
    QueryRewriteResult,
    SearchResult,
    RouteSearchResult,
)
from knowledge.services.keyword_retrieval_service import KeywordRetrievalService
from knowledge.services.multi_route_retrieval_service import MultiRouteRetrievalService
from threading import Event
from time import monotonic


class DualRouteRepository:
    def __init__(self):
        self.vector_queries: list[tuple[str, int, dict | None]] = []
        self.chunks = [
            KnowledgeChunk(
                chunk_id="keyword-sdk",
                heading="SDK 查询指标应用数据",
                content="使用 MetricClient.getDataV2 查询指标应用数据。",
                metadata={
                    "chunk_id": "keyword-sdk",
                    "heading": "SDK 查询指标应用数据",
                    "bm25_keywords": "SDK, MetricClient, getDataV2",
                    "app_id": "middle-platform",
                    "domain": "指标平台",
                    "name": "指标平台",
                    "chunk_type": "faq",
                },
            )
        ]

    @staticmethod
    def _matches(metadata, where):
        if not where:
            return True
        if "$and" in where:
            return all(DualRouteRepository._matches(metadata, item) for item in where["$and"])
        return all(metadata.get(key) == value for key, value in where.items())

    def get_keyword_index_records(self, where=None):
        return [
            KeywordIndexRecord(
                chunk_id=chunk.chunk_id,
                heading=chunk.heading,
                keywords=str(chunk.metadata.get("bm25_keywords", "")),
                metadata=dict(chunk.metadata),
            )
            for chunk in self.chunks
            if self._matches(chunk.metadata, where)
        ]

    def get_chunk_ids(self, where=None):
        return {
            chunk.chunk_id for chunk in self.chunks if self._matches(chunk.metadata, where)
        }

    def get_chunks(self, where=None, ids=None):
        allowed_ids = set(ids) if ids is not None else None
        return [
            chunk
            for chunk in self.chunks
            if (allowed_ids is None or chunk.chunk_id in allowed_ids)
            and self._matches(chunk.metadata, where)
        ]

    def search(self, query, k=5, where=None):
        self.vector_queries.append((query, k, where))
        return [
            SearchResult(
                chunk_id="vector-sdk",
                content="向量召回的 SDK 文档。",
                metadata={
                    "chunk_id": "vector-sdk",
                    "heading": "SDK 开放接口",
                    "app_id": "middle-platform",
                    "domain": "指标平台",
                    "name": "指标平台",
                    "chunk_type": "faq",
                },
                score=0.125,
            )
        ]


class FailingVectorRepository(DualRouteRepository):
    def search(self, query, k=5, where=None):
        self.vector_queries.append((query, k, where))
        raise RuntimeError("private provider error")


class CoordinatedKeywordService:
    app_id = "middle-platform"

    def __init__(self, keyword_started: Event, vector_started: Event):
        self.keyword_started = keyword_started
        self.vector_started = vector_started

    def build_where(self, where=None):
        return where

    def search(self, query, k=5, where=None, additional_queries=None):
        self.keyword_started.set()
        self.vector_started.wait(timeout=0.5)
        return [
            RouteSearchResult(
                retrieval_route="keyword",
                rank=1,
                chunk_id="keyword",
                heading="管理员转办",
                content="关键词证据",
                metadata={},
                raw_score=1.0,
                score_type="fielded_bm25",
                higher_is_better=True,
            )
        ]


class CoordinatedVectorRepository:
    def __init__(self, keyword_started: Event, vector_started: Event):
        self.keyword_started = keyword_started
        self.vector_started = vector_started

    def search(self, query, k=5, where=None):
        self.vector_started.set()
        self.keyword_started.wait(timeout=0.5)
        return [
            SearchResult(
                chunk_id="vector",
                content="向量证据",
                metadata={"heading": "管理员转办接口"},
                score=0.1,
            )
        ]


class PassthroughRanker:
    def rank(self, query, keyword_results, vector_results, top_k):
        return HybridRankResult(results=[], rerank_applied=False)


def test_keyword_and_vector_routes_run_in_parallel():
    keyword_started = Event()
    vector_started = Event()
    service = MultiRouteRetrievalService(
        CoordinatedVectorRepository(keyword_started, vector_started),
        CoordinatedKeywordService(keyword_started, vector_started),
        hybrid_ranker=PassthroughRanker(),
    )

    started = monotonic()
    result = service.search("管理员转办")
    elapsed = monotonic() - started

    assert elapsed < 0.3
    assert result.keyword_results[0].chunk_id == "keyword"
    assert result.vector_results[0].chunk_id == "vector"


def test_multi_route_search_falls_back_to_vector_when_bm25_fails(caplog):
    class FailingKeywordService:
        app_id = "middle-platform"

        def build_where(self, where=None):
            return where

        def search(self, *args, **kwargs):
            raise RuntimeError("private keyword error")

    repository = DualRouteRepository()
    service = MultiRouteRetrievalService(
        repository,
        FailingKeywordService(),
    )

    result = service.search("SDK 查询")

    assert result.keyword_results == []
    assert result.vector_results
    assert result.final_results
    assert result.final_results[0].retrieval_routes == ("vector",)
    assert "Keyword retrieval failed; continuing with vector results" in caplog.text
    assert "private keyword error" not in caplog.text


def test_multi_route_search_falls_back_to_bm25_when_vector_query_fails(caplog):
    repository = FailingVectorRepository()
    keyword_service = KeywordRetrievalService(
        repository,
        app_id="middle-platform",
        domain="指标平台",
    )
    service = MultiRouteRetrievalService(repository, keyword_service)

    result = service.search("SDK 怎么查询指标应用数据？")

    assert result.vector_results == []
    assert result.keyword_results
    assert result.final_results
    assert result.final_results[0].retrieval_routes == ("keyword",)
    assert "Vector retrieval failed; continuing with keyword results" in caplog.text
    assert "private provider error" not in caplog.text


def test_multi_route_search_applies_required_scope_to_both_routes():
    repository = DualRouteRepository()
    keyword_service = KeywordRetrievalService(
        repository,
        app_id="middle-platform",
        domain="指标平台",
    )
    service = MultiRouteRetrievalService(repository, keyword_service)

    result = service.search(
        "SDK 怎么查询指标应用数据？",
        keyword_k=3,
        vector_k=4,
        where={"chunk_type": "faq"},
    )

    assert result.keyword_results[0].retrieval_route == "keyword"
    assert result.vector_results[0].retrieval_route == "vector"
    assert result.vector_results[0].score_type == "chroma_distance"
    assert result.vector_results[0].higher_is_better is False
    assert repository.vector_queries == [
        (
            result.query,
            4,
            {
                "$and": [
                    {"app_id": "middle-platform"},
                    {"domain": "指标平台"},
                    {"chunk_type": "faq"},
                ]
            },
        )
    ]


class FakeQueryRewriter:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def rewrite(self, query, app_id):
        self.calls.append((query, app_id))
        return self.result


class FakeHybridRanker:
    def __init__(self):
        self.calls = []

    def rank(self, query, keyword_results, vector_results, top_k):
        self.calls.append((query, keyword_results, vector_results, top_k))
        source = keyword_results[0]
        return HybridRankResult(
            results=[
                FinalSearchResult(
                    rank=1,
                    chunk_id=source.chunk_id,
                    heading=source.heading,
                    content=source.content,
                    metadata=source.metadata,
                    retrieval_routes=("keyword",),
                    keyword_score=source.raw_score,
                    vector_distance=None,
                    fusion_score=0.1,
                    rerank_score=0.9,
                )
            ],
            rerank_applied=True,
        )


def test_multi_route_search_uses_rewritten_query_for_vector_and_keywords_for_bm25():
    repository = DualRouteRepository()
    rewriter = FakeQueryRewriter(
        QueryRewriteResult(
            original_query="这个咋查",
            retrieval_query="指标应用如何通过 getDataV2 查询数据？",
            keywords=("SDK", "MetricClient", "getDataV2"),
            domain_candidates=("指标平台",),
            rewrite_applied=True,
        )
    )
    keyword_service = KeywordRetrievalService(repository, app_id="middle-platform")
    hybrid_ranker = FakeHybridRanker()
    service = MultiRouteRetrievalService(
        repository,
        keyword_service,
        query_rewriter=rewriter,
        hybrid_ranker=hybrid_ranker,
    )

    result = service.search("这个咋查", keyword_k=3, vector_k=4)

    assert rewriter.calls == [("这个咋查", "middle-platform")]
    assert result.retrieval_query == "指标应用如何通过 getDataV2 查询数据？"
    assert result.extracted_keywords == ("SDK", "MetricClient", "getDataV2")
    assert result.keyword_results
    assert repository.vector_queries[0][0] == result.retrieval_query
    assert hybrid_ranker.calls[0][0] == result.retrieval_query
    assert hybrid_ranker.calls[0][3] == 5
    assert result.rerank_applied is True
    assert result.final_results[0].rerank_score == 0.9
    assert set(result.stage_timings_ms) == {
        "query_rewrite",
        "keyword_search",
        "vector_search",
        "rerank",
    }
    assert all(value >= 0 for value in result.stage_timings_ms.values())


def test_multi_route_search_skips_retrieval_when_rewriter_marks_it_unnecessary():
    repository = DualRouteRepository()
    rewriter = FakeQueryRewriter(
        QueryRewriteResult(
            original_query="你好",
            retrieval_query="你好",
            retrieval_needed=False,
            rewrite_applied=True,
        )
    )
    keyword_service = KeywordRetrievalService(repository, app_id="middle-platform")
    service = MultiRouteRetrievalService(repository, keyword_service, query_rewriter=rewriter)

    result = service.search("你好")

    assert result.keyword_results == []
    assert result.vector_results == []
    assert result.final_results == []
    assert result.retrieval_needed is False
    assert repository.vector_queries == []


class ExactPathRanker:
    def rank(self, query, keyword_results, vector_results, top_k):
        common = dict(
            keyword_score=1.0,
            vector_distance=0.1,
            fusion_score=0.1,
            rerank_score=0.8,
            retrieval_routes=("keyword",),
        )
        return HybridRankResult(
            rerank_applied=True,
            results=[
                FinalSearchResult(
                    rank=1, chunk_id="semantic", heading="实例详情",
                    content="相似内容", metadata={}, **common,
                ),
                FinalSearchResult(
                    rank=2, chunk_id="exact", heading="getInstanceDetail",
                    content="接口实现", metadata={
                        "relative_path": "gateway/sys/flow/process/instance/getInstanceDetail"
                    }, **common,
                ),
            ],
        )


def test_exact_original_identifier_is_preserved_for_bm25_and_boosts_final_result():
    repository = DualRouteRepository()
    keyword_service = KeywordRetrievalService(repository, app_id="middle-platform")
    service = MultiRouteRetrievalService(
        repository, keyword_service, hybrid_ranker=ExactPathRanker()
    )

    result = service.search(
        "gateway/gateway/sys/flow/process/instance/getInstanceDetail 入参"
    )

    assert "/gateway/sys/flow/process/instance/getInstanceDetail" in result.exact_identifiers
    assert result.final_results[0].chunk_id == "exact"
    assert result.final_results[0].rank == 1
