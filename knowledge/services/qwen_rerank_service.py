from typing import Any

from knowledge.schemas.documents import MergedSearchCandidate, RerankScore


MAX_RERANK_DOCUMENT_CHARS = 48000
MAX_RERANK_HEADING_CHARS = 4000
MAX_RERANK_KEYWORD_CHARS = 4000


class QwenRerankService:
    """阿里 qwen3-rerank API 适配器。

    上游 HybridRerankService 只关心 rerank(query, candidates, top_k) 这个协议；
    具体怎么请求阿里接口、怎么解析响应，都封装在这个类里。
    """

    def __init__(self, client: Any, model: str):
        self.client = client
        self.model = model

    def rerank(
        self,
        query: str,
        candidates: list[MergedSearchCandidate],
        top_k: int,
    ) -> list[RerankScore]:
        # qwen3-rerank 接收：
        # - query：用户检索问题
        # - documents：候选文本列表
        # - top_n：需要返回的候选数量
        #
        # 返回结果里的 index 指向 documents 的下标，
        # HybridRerankService 会用这个 index 找回原始候选。
        response = self.client.post(
            "/reranks",
            body={
                "model": self.model,
                "query": query,
                "documents": [self._candidate_text(candidate) for candidate in candidates],
                "top_n": min(top_k, len(candidates)),
                "instruct": "Given an enterprise support question, retrieve passages that answer it.",
            },
            cast_to=object,
        )
        raw_results = (
            response.get("results", [])
            if isinstance(response, dict)
            else getattr(response, "results", [])
        )
        # 统一转换成项目内部的 RerankScore 数据结构。
        return [
            RerankScore(
                index=int(
                    item.get("index") if isinstance(item, dict) else item.index
                ),
                relevance_score=float(
                    item.get("relevance_score")
                    if isinstance(item, dict)
                    else item.relevance_score
                ),
            )
            for item in raw_results
        ]

    @staticmethod
    def _candidate_text(candidate: MergedSearchCandidate) -> str:
        # 给 rerank 模型看的候选文本不是纯正文，而是“标题 + 关键词 + 正文”。
        # 这样模型能同时看到人工维护的 bm25_keywords，提高精排判断质量。
        heading = candidate.heading[:MAX_RERANK_HEADING_CHARS]
        keywords = str(candidate.metadata.get("bm25_keywords", ""))[
            :MAX_RERANK_KEYWORD_CHARS
        ]
        prefix = f"标题：{heading}\n关键词：{keywords}\n正文："
        content_budget = max(MAX_RERANK_DOCUMENT_CHARS - len(prefix), 0)
        return prefix + candidate.content[:content_budget]
