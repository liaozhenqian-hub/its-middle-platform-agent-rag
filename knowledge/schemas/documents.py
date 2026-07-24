from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class KnowledgeChunk:
    """知识库最基本的文本块。

    入库时，一个 Markdown section 会变成一个或多个 KnowledgeChunk。
    content 是真正用于 embedding 和展示的正文；metadata 保存检索过滤和辅助排序字段。
    """

    chunk_id: str
    content: str
    heading: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class KeywordIndexRecord:
    """BM25 内存索引用的轻量记录。

    它刻意不带 content，只带 heading 和 keywords，
    这样 refresh() 构建 BM25 索引时不用加载所有长正文。
    """

    chunk_id: str
    heading: str
    keywords: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MarkdownLoadResult:
    source_path: str
    frontmatter: dict[str, Any]
    chunks: list[KnowledgeChunk]


@dataclass(frozen=True)
class SearchResult:
    """向量检索的原始返回结构。

    score 来自 Chroma similarity_search_with_score，在本项目里被当作 distance 使用。
    """

    chunk_id: str
    content: str
    metadata: dict[str, Any]
    score: float | None = None


@dataclass(frozen=True)
class RouteSearchResult:
    """单一路线的召回结果。

    keyword route 和 vector route 都会被包装成这个结构。
    higher_is_better 用来标记 raw_score 的方向：
    - BM25: 分数越大越好
    - Chroma distance: 距离越小越好
    """

    retrieval_route: str
    rank: int
    chunk_id: str
    heading: str
    content: str
    metadata: dict[str, Any]
    raw_score: float
    score_type: str
    higher_is_better: bool


@dataclass(frozen=True)
class MultiRouteSearchResult:
    """多路召回的完整结果包。

    它同时保留：
    - keyword_results：关键词路线候选
    - vector_results：向量路线候选
    - final_results：融合/精排后的最终结果

    这样 CLI 或调试工具可以看到每一步发生了什么。
    """

    query: str
    keyword_results: list[RouteSearchResult]
    vector_results: list[RouteSearchResult]
    retrieval_query: str = ""
    extracted_keywords: tuple[str, ...] = ()
    retrieval_needed: bool = True
    clarification_needed: bool = False
    rewrite_applied: bool = False
    final_results: list["FinalSearchResult"] = field(default_factory=list)
    rerank_applied: bool = False
    stage_timings_ms: dict[str, float] = field(default_factory=dict)
    exact_identifiers: tuple[str, ...] = ()


@dataclass(frozen=True)
class QueryRewriteResult:
    """查询改写结果。

    retrieval_query 用于向量检索和 rerank；
    keywords 用于增强 BM25；
    retrieval_needed=false 时会跳过后续召回流程。
    """

    original_query: str
    retrieval_query: str
    keywords: tuple[str, ...] = ()
    domain_candidates: tuple[str, ...] = ()
    retrieval_needed: bool = True
    clarification_needed: bool = False
    rewrite_applied: bool = False
    task_type: str = "unknown"


@dataclass(frozen=True)
class MergedSearchCandidate:
    """按 chunk_id 去重后的融合候选。

    一个候选可能来自 keyword、vector 或两路同时命中。
    fusion_score 是 RRF 融合分；rerank 之前会先按它做默认排序。
    """

    chunk_id: str
    heading: str
    content: str
    metadata: dict[str, Any]
    retrieval_routes: tuple[str, ...]
    keyword_score: float | None = None
    vector_distance: float | None = None
    fusion_score: float = 0.0


@dataclass(frozen=True)
class RerankScore:
    """rerank 模型返回的排序分。

    index 指向传给 reranker 的候选列表下标。
    """

    index: int
    relevance_score: float


@dataclass(frozen=True)
class FinalSearchResult:
    """最终对外展示的检索结果。

    这里保留多种分数，便于解释排序来源：
    keyword_score、vector_distance、fusion_score、rerank_score。
    """

    rank: int
    chunk_id: str
    heading: str
    content: str
    metadata: dict[str, Any]
    retrieval_routes: tuple[str, ...]
    keyword_score: float | None
    vector_distance: float | None
    fusion_score: float
    rerank_score: float | None = None


@dataclass(frozen=True)
class HybridRankResult:
    results: list[FinalSearchResult]
    rerank_applied: bool


@dataclass(frozen=True)
class IngestionSummary:
    source_path: str
    chunk_count: int
    parent_chunk_count: int
    stored_count: int
