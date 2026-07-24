from types import SimpleNamespace

from knowledge.schemas.documents import MergedSearchCandidate, RerankScore, RouteSearchResult
from knowledge.services.hybrid_rerank_service import HybridRerankService
from knowledge.services.qwen_rerank_service import QwenRerankService


def _result(chunk_id, route, rank, score, heading=None):
    return RouteSearchResult(
        retrieval_route=route,
        rank=rank,
        chunk_id=chunk_id,
        heading=heading or chunk_id,
        content=f"{chunk_id} full content",
        metadata={"bm25_keywords": f"{chunk_id}, SDK"},
        raw_score=score,
        score_type="fielded_bm25" if route == "keyword" else "chroma_distance",
        higher_is_better=route == "keyword",
    )


class FakeReranker:
    def __init__(self, scores=None, error=None):
        self.scores = scores or []
        self.error = error
        self.calls = []

    def rerank(self, query, candidates, top_k):
        self.calls.append((query, candidates, top_k))
        if self.error:
            raise self.error
        return self.scores


def test_hybrid_rerank_deduplicates_routes_and_uses_cross_encoder_order():
    reranker = FakeReranker(
        [RerankScore(index=1, relevance_score=0.95), RerankScore(index=0, relevance_score=0.7)]
    )
    service = HybridRerankService(reranker=reranker)

    result = service.rank(
        query="SDK 怎么查询",
        keyword_results=[
            _result("shared", "keyword", 1, 1.0),
            _result("keyword-only", "keyword", 2, 0.8),
        ],
        vector_results=[
            _result("shared", "vector", 1, 0.1),
            _result("vector-only", "vector", 2, 0.2),
        ],
        top_k=2,
    )

    assert result.rerank_applied is True
    assert len(reranker.calls[0][1]) == 3
    assert [item.chunk_id for item in result.results] == ["keyword-only", "shared"]
    shared = result.results[1]
    assert shared.retrieval_routes == ("keyword", "vector")
    assert shared.keyword_score == 1.0
    assert shared.vector_distance == 0.1
    assert shared.rerank_score == 0.7


def test_hybrid_rerank_falls_back_to_rrf_when_provider_fails(caplog):
    service = HybridRerankService(
        reranker=FakeReranker(error=RuntimeError("rerank unavailable"))
    )

    with caplog.at_level(
        "WARNING",
        logger="knowledge.services.hybrid_rerank_service",
    ):
        result = service.rank(
            query="SDK",
            keyword_results=[_result("shared", "keyword", 1, 1.0)],
            vector_results=[
                _result("vector-only", "vector", 1, 0.1),
                _result("shared", "vector", 2, 0.2),
            ],
            top_k=2,
        )

    assert result.rerank_applied is False
    assert result.results[0].chunk_id == "shared"
    assert result.results[0].rerank_score is None
    assert result.results[0].fusion_score > result.results[1].fusion_score
    assert "Rerank failed" in caplog.text
    assert "query='SDK'" in caplog.text
    assert "candidate_count=2" in caplog.text
    assert "rerank unavailable" in caplog.text


class FakeOpenAIClient:
    def __init__(self):
        self.calls = []

    def post(self, path, body, cast_to):
        self.calls.append((path, body, cast_to))
        return {
            "results": [
                {"index": 1, "relevance_score": 0.92},
                {"index": 0, "relevance_score": 0.61},
            ]
        }


def test_qwen_reranker_sends_query_and_candidate_text_to_rerank_endpoint():
    client = FakeOpenAIClient()
    service = QwenRerankService(client=client, model="qwen3-rerank")
    candidates = HybridRerankService().merge(
        keyword_results=[_result("sdk", "keyword", 1, 1.0, heading="SDK 查询")],
        vector_results=[_result("sql", "vector", 1, 0.1, heading="SQL 预览")],
    )

    scores = service.rerank("怎么查 SDK", candidates, top_k=2)

    assert scores == [
        RerankScore(index=1, relevance_score=0.92),
        RerankScore(index=0, relevance_score=0.61),
    ]
    path, body, _ = client.calls[0]
    assert path == "/reranks"
    assert body["model"] == "qwen3-rerank"
    assert body["query"] == "怎么查 SDK"
    assert body["top_n"] == 2
    assert "标题：SDK 查询" in body["documents"][0]
    assert "关键词：sdk, SDK" in body["documents"][0]
    assert "正文：sdk full content" in body["documents"][0]


def test_qwen_reranker_bounds_each_document_below_provider_limit():
    client = FakeOpenAIClient()
    service = QwenRerankService(client=client, model="qwen3-rerank")
    candidate = MergedSearchCandidate(
        chunk_id="large-code",
        heading="ImportantService.handleRequest" + "H" * 10000,
        content="public void handleRequest()" + "C" * 100000,
        metadata={"bm25_keywords": "handleRequest," + "K" * 10000},
        retrieval_routes=("keyword", "vector"),
    )

    service.rerank("为什么请求失败", [candidate], top_k=1)

    document = client.calls[0][1]["documents"][0]
    assert len(document) <= 48000
    assert document.startswith("标题：ImportantService.handleRequest")
    assert "\n关键词：handleRequest," in document
    assert "\n正文：public void handleRequest()" in document
