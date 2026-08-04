import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from time import perf_counter
from typing import Any, Protocol

from knowledge.repositories.vector_store_repository import VectorStoreRepository
from knowledge.schemas.documents import (
    MultiRouteSearchResult,
    QueryRewriteResult,
    RouteSearchResult,
)
from knowledge.services.keyword_retrieval_service import KeywordRetrievalService
from knowledge.services.query_identifiers import extract_exact_identifiers
from knowledge.services.hybrid_rerank_service import HybridRerankService
from knowledge.services.provider_circuit import NonRetryableProviderCircuit


logger = logging.getLogger(__name__)


class QueryRewriter(Protocol):
    def rewrite(self, query: str, app_id: str) -> QueryRewriteResult: ...


class HybridRanker(Protocol):
    def rank(self, query, keyword_results, vector_results, top_k): ...


class MultiRouteRetrievalService:
    """多路召回总编排器。

    这里的“多路”主要指两条召回路线：
    1. keyword route：基于 jieba 分词 + BM25 的关键词召回。
    2. vector route：基于 embedding + Chroma 的向量召回。

    注意：这层不会直接把 BM25 分数和向量距离相加，因为两者量纲不同。
    它只负责分别取回两路候选，再交给 hybrid_ranker 做去重、融合和精排。
    """

    def __init__(
        self,
        repository: VectorStoreRepository,
        keyword_service: KeywordRetrievalService,
        query_rewriter: QueryRewriter | None = None,
        hybrid_ranker: HybridRanker | None = None,
        parallel_routes_enabled: bool = True,
        provider_failure_cooldown_seconds: float = 60.0,
    ):
        self.repository = repository
        self.keyword_service = keyword_service
        self.query_rewriter = query_rewriter
        self.hybrid_ranker = hybrid_ranker or HybridRerankService()
        self.parallel_routes_enabled = parallel_routes_enabled
        self.vector_circuit = NonRetryableProviderCircuit(
            provider_failure_cooldown_seconds
        )

    def search(
        self,
        query: str,
        keyword_k: int = 5,
        vector_k: int = 5,
        final_k: int = 5,
        where: dict[str, Any] | None = None,
    ) -> MultiRouteSearchResult:
        # 第一步：查询改写。
        # 如果配置了 LLM query_rewriter，就让它把口语问题改写成更适合检索的问题；
        # 如果没配置，直接把原始 query 当作 retrieval_query。
        timings: dict[str, float] = {}
        exact_identifiers = extract_exact_identifiers(query)
        started = perf_counter()
        rewrite = (
            self.query_rewriter.rewrite(query, self.keyword_service.app_id)
            if self.query_rewriter is not None
            else QueryRewriteResult(
                original_query=query,
                retrieval_query=query,
            )
        )
        timings["query_rewrite"] = (perf_counter() - started) * 1000
        # LLM 可以判断“这个问题不需要查知识库”，例如“你好”“谢谢”。
        # 这种情况下直接返回空召回结果，避免浪费 BM25、向量检索和 rerank 调用。
        if not rewrite.retrieval_needed:
            return MultiRouteSearchResult(
                query=query,
                retrieval_query=rewrite.retrieval_query,
                extracted_keywords=rewrite.keywords,
                retrieval_needed=False,
                clarification_needed=rewrite.clarification_needed,
                rewrite_applied=rewrite.rewrite_applied,
                keyword_results=[],
                vector_results=[],
                stage_timings_ms=timings,
                exact_identifiers=exact_identifiers,
            )
        # 第二步：准备 BM25 额外查询文本。
        # BM25 适合精确词匹配，所以它不仅吃原始问题，还吃：
        # - 改写后的完整检索问题
        # - LLM 提取出的关键词列表
        # 这样可以降低“口语改写把接口名/字段名弄丢”的风险。
        additional_queries = [rewrite.retrieval_query]
        if rewrite.keywords:
            additional_queries.append(" ".join(rewrite.keywords))
        if exact_identifiers:
            additional_queries.append(" ".join(exact_identifiers))

        # 第三、四步：关键词与向量召回。查询改写完成后两路互不依赖，
        # 因此默认并行执行；任一路失败时保留另一路证据。
        def run_keyword_route():
            route_started = perf_counter()
            try:
                results = self.keyword_service.search(
                    query,
                    k=keyword_k,
                    where=where,
                    additional_queries=additional_queries,
                )
            except Exception as exc:
                logger.warning(
                    "Keyword retrieval failed; continuing with vector results error_type=%s",
                    type(exc).__name__,
                )
                results = []
            return results, (perf_counter() - route_started) * 1000

        def run_vector_route():
            route_started = perf_counter()
            if not self.vector_circuit.allow():
                return [], (perf_counter() - route_started) * 1000
            try:
                results = self.repository.search(
                    rewrite.retrieval_query,
                    k=vector_k,
                    where=self.keyword_service.build_where(where),
                )
                self.vector_circuit.record_success()
            except Exception as exc:
                self.vector_circuit.record_failure(exc)
                logger.warning(
                    "Vector retrieval failed; continuing with keyword results error_type=%s",
                    type(exc).__name__,
                )
                results = []
            return results, (perf_counter() - route_started) * 1000

        if self.parallel_routes_enabled:
            with ThreadPoolExecutor(
                max_workers=2,
                thread_name_prefix="retrieval-route",
            ) as executor:
                keyword_future = executor.submit(run_keyword_route)
                vector_future = executor.submit(run_vector_route)
                keyword_results, keyword_elapsed = keyword_future.result()
                vector_results, vector_elapsed = vector_future.result()
        else:
            keyword_results, keyword_elapsed = run_keyword_route()
            vector_results, vector_elapsed = run_vector_route()
        timings["keyword_search"] = keyword_elapsed
        timings["vector_search"] = vector_elapsed

        # Chroma 返回的是通用 SearchResult，这里统一包装成 RouteSearchResult。
        # 这样后面的 hybrid_ranker 可以用同一种结构处理 keyword/vector 两路结果。
        #
        # Chroma score 在这里被当作 distance：越小越相似，
        # 所以 higher_is_better=False，和 BM25 的“越大越好”区分开。
        route_vector_results = [
            RouteSearchResult(
                retrieval_route="vector",
                rank=rank,
                chunk_id=result.chunk_id,
                heading=str(result.metadata.get("heading", "")),
                content=result.content,
                metadata=dict(result.metadata),
                raw_score=float(result.score or 0.0),
                score_type="chroma_distance",
                higher_is_better=False,
            )
            for rank, result in enumerate(vector_results, start=1)
        ]

        # 第五步：融合排序。
        # hybrid_ranker 会先按 chunk_id 合并去重，再优先调用 reranker；
        # reranker 不可用或失败时，退回到 RRF 融合排序。
        started = perf_counter()
        hybrid_result = self.hybrid_ranker.rank(
            rewrite.retrieval_query,
            keyword_results,
            route_vector_results,
            final_k,
        )
        timings["rerank"] = (perf_counter() - started) * 1000
        final_results = self._boost_exact_matches(
            hybrid_result.results, exact_identifiers
        )

        # 第六步：把“过程结果”和“最终结果”都返回。
        # CLI 会分别打印 Keyword Results、Vector Results、Final Results，
        # 这对调试召回效果很有用。
        return MultiRouteSearchResult(
            query=query,
            retrieval_query=rewrite.retrieval_query,
            extracted_keywords=rewrite.keywords,
            retrieval_needed=True,
            clarification_needed=rewrite.clarification_needed,
            rewrite_applied=rewrite.rewrite_applied,
            keyword_results=keyword_results,
            vector_results=route_vector_results,
            final_results=final_results,
            rerank_applied=hybrid_result.rerank_applied,
            stage_timings_ms=timings,
            exact_identifiers=exact_identifiers,
        )

    @staticmethod
    def _boost_exact_matches(results, identifiers):
        if not identifiers:
            return list(results)

        def match_count(item) -> int:
            metadata = item.metadata or {}
            searchable = "\n".join(
                str(value)
                for value in (
                    item.heading,
                    item.content,
                    metadata.get("relative_path"),
                    metadata.get("symbol_name"),
                    metadata.get("bm25_keywords"),
                )
                if value
            ).casefold()
            return sum(
                1
                for identifier in identifiers
                if identifier.casefold().lstrip("/") in searchable
            )

        ordered = sorted(
            enumerate(results),
            key=lambda pair: (-match_count(pair[1]), pair[0]),
        )
        return [replace(item, rank=rank) for rank, (_, item) in enumerate(ordered, 1)]
