from typing import Any, Protocol

from rank_bm25 import BM25Plus

from knowledge.retrieval.tokenizer import JiebaSearchTokenizer
from knowledge.schemas.documents import KeywordIndexRecord, KnowledgeChunk, RouteSearchResult


class ChunkRepository(Protocol):
    """关键词召回需要的仓储接口。

    这里故意只声明 BM25 需要的最小能力，方便测试时用内存假仓储替代 Chroma。
    """

    def get_keyword_index_records(
        self,
        where: dict[str, Any] | None = None,
    ) -> list[KeywordIndexRecord]: ...

    def get_chunk_ids(
        self,
        where: dict[str, Any] | None = None,
    ) -> set[str]: ...

    def get_chunks(
        self,
        where: dict[str, Any] | None = None,
        ids: list[str] | None = None,
    ) -> list[KnowledgeChunk]: ...


class KeywordRetrievalService:
    """基于字段化 BM25 的关键词召回服务。

    “字段化”的意思是：标题 heading 和人工维护的 bm25_keywords 分开建索引、
    分开打分，最后按权重合成一个关键词召回分。

    这个服务是内存索引：初始化或 refresh() 时从 Chroma 读取轻量 metadata，
    后续 search() 在内存里算 BM25，只有选出 Top K 后才回仓储拿完整正文。
    """

    def __init__(
        self,
        repository: ChunkRepository,
        app_id: str,
        domain: str | None = None,
        name: str | None = None,
        title_weight: float = 0.65,
        keywords_weight: float = 0.35,
        tokenizer: JiebaSearchTokenizer | None = None,
    ):
        normalized_app_id = app_id.strip()
        if not normalized_app_id:
            raise ValueError("app_id is required")
        if title_weight < 0 or keywords_weight < 0:
            raise ValueError("BM25 field weights cannot be negative")
        if title_weight + keywords_weight <= 0:
            raise ValueError("At least one BM25 field weight must be positive")
        self.repository = repository
        self.app_id = normalized_app_id
        self.domain = domain.strip() if domain and domain.strip() else None
        self.name = name.strip() if name and name.strip() else None
        # scope_where 是数据隔离条件。
        # app_id 必填；domain/name 可选。后续所有 BM25 检索都会先限制在这个范围内。
        self.scope_where = {
            key: value
            for key, value in {
                "app_id": self.app_id,
                "domain": self.domain,
                "name": self.name,
            }.items()
            if value is not None
        }
        self.title_weight = title_weight
        self.keywords_weight = keywords_weight
        self.tokenizer = tokenizer or JiebaSearchTokenizer()
        self.refresh()

    def refresh(self) -> None:
        # 从仓储读取用于关键词索引的轻量记录：
        # chunk_id + heading + bm25_keywords + metadata。
        # 注意这里不取正文，避免初始化 BM25 时把所有长文本都加载进来。
        self._records = self.repository.get_keyword_index_records(
            where=self.scope_where
        )
        # 建立 chunk_id -> records 下标的映射，后面根据过滤条件快速定位候选。
        self._record_indexes = {
            record.chunk_id: index for index, record in enumerate(self._records)
        }
        # 标题字段单独分词、单独建 BM25。
        # 标题通常很短，但对用户问题的意图命中非常强。
        self._title_tokens = [
            self.tokenizer.tokenize(record.heading) for record in self._records
        ]
        # bm25_keywords 字段也单独分词、单独建 BM25。
        # 这个字段适合放接口名、方法名、字段名、别名等“用户可能会搜”的词。
        self._keyword_tokens = [
            self.tokenizer.tokenize(record.keywords) for record in self._records
        ]
        self._title_index = self._build_index(self._title_tokens)
        self._keywords_index = self._build_index(self._keyword_tokens)

    def search(
        self,
        query: str,
        k: int = 5,
        where: dict[str, Any] | None = None,
        additional_queries: list[str] | tuple[str, ...] | None = None,
    ) -> list[RouteSearchResult]:
        if k < 1:
            raise ValueError("k must be at least 1")

        # BM25 的查询文本由三部分组成：
        # 1. 原始 query
        # 2. LLM 改写后的 retrieval_query
        # 3. LLM 提取的关键词字符串
        # 这能兼顾“用户原话里的精确词”和“改写后的完整语义”。
        query_texts = [query, *(additional_queries or [])]
        query_tokens = list(
            dict.fromkeys(
                token
                for query_text in query_texts
                if query_text and query_text.strip()
                for token in self.tokenizer.tokenize(query_text)
            )
        )
        # 没有可检索 token，或者当前 scope 下没有 chunk，就直接返回空。
        if not query_tokens or not self._records:
            return []

        # eligible_ids 表示本次 search 真正允许参与排序的 chunk。
        # where=None 时，使用初始化时 scope_where 覆盖的所有记录；
        # where 不为空时，会把 app_id/domain/name 与额外 where 合并后去仓储过滤。
        eligible_ids = (
            set(self._record_indexes)
            if where is None
            else self.repository.get_chunk_ids(where=self.build_where(where))
        )
        eligible_indexes = {
            self._record_indexes[chunk_id]
            for chunk_id in eligible_ids
            if chunk_id in self._record_indexes
        }
        # 分别计算标题字段和关键词字段的 BM25 原始分。
        title_scores = self._scores(
            self._title_index,
            query_tokens,
            self._title_tokens,
        )
        keyword_scores = self._scores(
            self._keywords_index,
            query_tokens,
            self._keyword_tokens,
        )
        # 在“本次 eligible 候选集合内部”做归一化。
        # 这样 metadata filter 缩小范围时，分数仍然在当前候选集内可比较。
        normalized_titles = self._normalize(title_scores, eligible_indexes)
        normalized_keywords = self._normalize(keyword_scores, eligible_indexes)

        candidates: list[tuple[float, KeywordIndexRecord]] = []
        for chunk_id in eligible_ids:
            index = self._record_indexes.get(chunk_id)
            if index is None:
                continue
            score = (
                normalized_titles[index] * self.title_weight
                + normalized_keywords[index] * self.keywords_weight
            )
            if score > 0:
                candidates.append((score, self._records[index]))

        # BM25 route 内部按分数降序排序；chunk_id 作为稳定的次级排序键。
        candidates.sort(key=lambda item: (-item[0], item[1].chunk_id))
        selected = candidates[:k]
        if not selected:
            return []

        # 到这一步才回仓储取完整正文。
        # 这是一个小优化：BM25 索引只需要 heading/keywords，不需要长 content。
        chunks_by_id = {
            chunk.chunk_id: chunk
            for chunk in self.repository.get_chunks(
                ids=[record.chunk_id for _, record in selected]
            )
        }
        results: list[RouteSearchResult] = []
        for score, record in selected:
            chunk = chunks_by_id.get(record.chunk_id)
            if chunk is None:
                continue
            # 统一包装成 RouteSearchResult，后续可以和 vector route 用同一种结构合并。
            results.append(
                RouteSearchResult(
                    retrieval_route="keyword",
                    rank=len(results) + 1,
                    chunk_id=chunk.chunk_id,
                    heading=chunk.heading,
                    content=chunk.content,
                    metadata=dict(chunk.metadata),
                    raw_score=float(score),
                    score_type="fielded_bm25",
                    higher_is_better=True,
                )
            )
        return results

    def build_where(
        self,
        additional_where: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # 把初始化时的 scope_where 和调用方传入的 where 合并。
        # 例如：
        # scope_where={"app_id": "middle-platform", "domain": "指标平台"}
        # additional_where={"chunk_type": "faq"}
        # 会合并成 Chroma 可理解的 {"$and": [...]}。
        if not additional_where:
            return dict(self.scope_where)
        clauses = self._where_clauses(self.scope_where)
        clauses.extend(self._where_clauses(additional_where))
        return clauses[0] if len(clauses) == 1 else {"$and": clauses}

    @staticmethod
    def _where_clauses(where: dict[str, Any]) -> list[dict[str, Any]]:
        if set(where) == {"$and"} and isinstance(where["$and"], list):
            return list(where["$and"])
        if any(key.startswith("$") for key in where):
            return [where]
        return [{key: value} for key, value in where.items()]

    @staticmethod
    def _build_index(tokenized_corpus: list[list[str]]) -> BM25Plus | None:
        # 空语料无法构建 BM25 索引，用 None 表示“该字段不可打分”。
        if not tokenized_corpus or not any(tokenized_corpus):
            return None
        return BM25Plus(tokenized_corpus)

    @staticmethod
    def _scores(
        index: BM25Plus | None,
        query_tokens: list[str],
        tokenized_corpus: list[list[str]],
    ) -> list[float]:
        if index is None:
            return [0.0] * len(tokenized_corpus)
        query_token_set = set(query_tokens)
        # rank_bm25 的 BM25Plus 可能给没有任何词交集的文档一个非零基线分。
        # 这里手动要求 query token 与文档 token 至少有交集，避免无关文档混入候选。
        return [
            float(score) if query_token_set.intersection(document_tokens) else 0.0
            for score, document_tokens in zip(
                index.get_scores(query_tokens),
                tokenized_corpus,
            )
        ]

    @staticmethod
    def _normalize(
        scores: list[float],
        eligible_indexes: set[int] | None = None,
    ) -> list[float]:
        if not scores:
            return []
        indexes = (
            eligible_indexes
            if eligible_indexes is not None
            else set(range(len(scores)))
        )
        # 取候选集合中的最大值做 max-normalization。
        # 分母为 0 时说明没有有效命中，全部归零。
        maximum = max((scores[index] for index in indexes), default=0.0)
        maximum = max(maximum, 0.0)
        if maximum == 0:
            return [0.0] * len(scores)
        return [max(score, 0.0) / maximum for score in scores]
