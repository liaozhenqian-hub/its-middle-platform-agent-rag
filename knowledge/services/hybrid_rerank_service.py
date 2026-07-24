from dataclasses import replace
import logging
from typing import Protocol

from knowledge.schemas.documents import (
    FinalSearchResult,
    HybridRankResult,
    MergedSearchCandidate,
    RerankScore,
    RouteSearchResult,
)


logger = logging.getLogger(__name__)


class Reranker(Protocol):
    def rerank(
        self,
        query: str,
        candidates: list[MergedSearchCandidate],
        top_k: int,
    ) -> list[RerankScore]: ...


class HybridRerankService:
    """多路结果融合与重排服务。

    这个类做两件事：
    1. merge()：把 keyword/vector 两路结果按 chunk_id 去重，并计算 RRF 融合分。
    2. rank()：如果有外部 reranker，就让 reranker 做最终排序；否则使用 RRF 排序兜底。

    这里没有把 BM25 分数和向量距离直接相加，因为它们不是同一种分数。
    RRF 只依赖每一路内部 rank，因此适合作为不同召回路线之间的稳健融合方式。
    """

    def __init__(self, reranker: Reranker | None = None, rrf_k: int = 60):
        self.reranker = reranker
        self.rrf_k = rrf_k

    def merge(
        self,
        keyword_results: list[RouteSearchResult],
        vector_results: list[RouteSearchResult],
    ) -> list[MergedSearchCandidate]:
        # 用 chunk_id 做去重键。
        # 同一个 chunk 如果同时被 BM25 和向量召回，只保留一份正文，
        # 但 retrieval_routes 会记录它来自哪些路线。
        candidates: dict[str, MergedSearchCandidate] = {}
        for result in [*keyword_results, *vector_results]:
            # RRF: Reciprocal Rank Fusion。
            # 只看每一路内部排名，不关心原始分数量纲。
            # rank 越靠前，贡献越大；rrf_k 越大，不同名次之间的差异越平滑。
            contribution = 1.0 / (self.rrf_k + result.rank)
            existing = candidates.get(result.chunk_id)
            if existing is None:
                # 第一次遇到该 chunk：创建融合候选。
                candidates[result.chunk_id] = MergedSearchCandidate(
                    chunk_id=result.chunk_id,
                    heading=result.heading,
                    content=result.content,
                    metadata=dict(result.metadata),
                    retrieval_routes=(result.retrieval_route,),
                    keyword_score=(
                        result.raw_score if result.retrieval_route == "keyword" else None
                    ),
                    vector_distance=(
                        result.raw_score if result.retrieval_route == "vector" else None
                    ),
                    fusion_score=contribution,
                )
                continue

            # 第二次或更多次遇到同一个 chunk：说明它被多路召回命中。
            # 这里更新 routes、保留每一路自己的原始分数，并累加 RRF 贡献。
            routes = existing.retrieval_routes
            if result.retrieval_route not in routes:
                routes = (*routes, result.retrieval_route)
            candidates[result.chunk_id] = replace(
                existing,
                retrieval_routes=routes,
                keyword_score=(
                    result.raw_score
                    if result.retrieval_route == "keyword"
                    else existing.keyword_score
                ),
                vector_distance=(
                    result.raw_score
                    if result.retrieval_route == "vector"
                    else existing.vector_distance
                ),
                fusion_score=existing.fusion_score + contribution,
            )

        # merge 阶段的默认排序是 RRF 分数降序。
        # 如果后续 reranker 可用，这个顺序也是传给 reranker 的候选顺序；
        # reranker 返回的 index 指的就是这个 candidates 列表中的下标。
        return sorted(
            candidates.values(),
            key=lambda candidate: (-candidate.fusion_score, candidate.chunk_id),
        )

    def rank(
        self,
        query: str,
        keyword_results: list[RouteSearchResult],
        vector_results: list[RouteSearchResult],
        top_k: int,
    ) -> HybridRankResult:
        candidates = self.merge(keyword_results, vector_results)
        if not candidates:
            return HybridRankResult(results=[], rerank_applied=False)

        # 优先使用外部 cross-encoder reranker。
        # reranker 会同时看 query 和候选正文，通常比单纯 RRF 更懂“是否真正回答问题”。
        if self.reranker is not None:
            try:
                scores = self.reranker.rerank(query, candidates, top_k)
                results = [
                    self._final_result(
                        candidates[score.index],
                        rank=rank,
                        rerank_score=score.relevance_score,
                    )
                    for rank, score in enumerate(scores[:top_k], start=1)
                    if 0 <= score.index < len(candidates)
                ]
                if results:
                    return HybridRankResult(results=results, rerank_applied=True)
            except Exception:
                # rerank 是增强能力，不应该让检索整体失败。
                # 失败时继续走下面的 RRF 兜底结果。
                logger.warning(
                    "Rerank failed; using RRF fallback query=%r candidate_count=%d top_k=%d",
                    query,
                    len(candidates),
                    top_k,
                    exc_info=True,
                )

        # reranker 不存在、调用失败、或没有返回有效结果时：
        # 直接使用 merge() 产出的 RRF 排序作为 Final Results。
        return HybridRankResult(
            results=[
                self._final_result(candidate, rank=rank)
                for rank, candidate in enumerate(candidates[:top_k], start=1)
            ],
            rerank_applied=False,
        )

    @staticmethod
    def _final_result(
        candidate: MergedSearchCandidate,
        rank: int,
        rerank_score: float | None = None,
    ) -> FinalSearchResult:
        # 把融合候选转换成最终对外展示的结果结构。
        # 这里保留 keyword_score/vector_distance/fusion_score/rerank_score，
        # 方便排查“为什么这个 chunk 排在前面”。
        return FinalSearchResult(
            rank=rank,
            chunk_id=candidate.chunk_id,
            heading=candidate.heading,
            content=candidate.content,
            metadata=dict(candidate.metadata),
            retrieval_routes=candidate.retrieval_routes,
            keyword_score=candidate.keyword_score,
            vector_distance=candidate.vector_distance,
            fusion_score=candidate.fusion_score,
            rerank_score=rerank_score,
        )
