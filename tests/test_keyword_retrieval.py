import pytest

from knowledge.retrieval.tokenizer import JiebaSearchTokenizer
from knowledge.schemas.documents import KeywordIndexRecord, KnowledgeChunk
from knowledge.services.keyword_retrieval_service import KeywordRetrievalService


class InMemoryChunkRepository:
    def __init__(self, chunks: list[KnowledgeChunk]):
        self.chunks = chunks
        self.index_filters: list[dict | None] = []
        self.chunk_id_requests: list[dict | None] = []
        self.body_requests: list[list[str] | None] = []

    @staticmethod
    def _matches(metadata, where):
        if not where:
            return True
        if "$and" in where:
            return all(InMemoryChunkRepository._matches(metadata, item) for item in where["$and"])
        if "$or" in where:
            return any(InMemoryChunkRepository._matches(metadata, item) for item in where["$or"])
        return all(metadata.get(key) == value for key, value in where.items())

    def get_keyword_index_records(self, where=None):
        self.index_filters.append(where)
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
        self.chunk_id_requests.append(where)
        return {
            chunk.chunk_id
            for chunk in self.chunks
            if self._matches(chunk.metadata, where)
        }

    def get_chunks(self, where=None, ids=None):
        self.body_requests.append(ids)
        allowed_ids = set(ids) if ids is not None else None
        return [
            chunk
            for chunk in self.chunks
            if (allowed_ids is None or chunk.chunk_id in allowed_ids)
            and self._matches(chunk.metadata, where)
        ]


def _chunk(
    chunk_id: str,
    heading: str,
    keywords: str,
    *,
    app_id: str = "middle-platform",
    domain: str = "指标平台",
    name: str = "指标平台",
    chunk_type: str = "faq",
) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=chunk_id,
        heading=heading,
        content=f"{heading}\n这是 {chunk_id} 的完整正文。",
        metadata={
            "chunk_id": chunk_id,
            "heading": heading,
            "bm25_keywords": keywords,
            "app_id": app_id,
            "domain": domain,
            "name": name,
            "chunk_type": chunk_type,
        },
    )


def test_tokenizer_preserves_technical_identifiers_and_api_paths():
    tokenizer = JiebaSearchTokenizer()

    tokens = tokenizer.tokenize(
        "请问 SDK 的 MetricClient.getDataV2 怎么调用 /api/datacenter/v2/getData？"
    )

    assert "sdk" in tokens
    assert "metricclient.getdatav2" in tokens
    assert "/api/datacenter/v2/getdata" in tokens
    assert "请问" not in tokens
    assert "怎么" not in tokens
    assert "？" not in tokens


def test_app_id_is_required_for_keyword_index_scope():
    repository = InMemoryChunkRepository([])

    with pytest.raises(ValueError, match="app_id is required"):
        KeywordRetrievalService(repository, app_id="")


def test_refresh_loads_lightweight_records_for_required_app_id():
    repository = InMemoryChunkRepository(
        [
            _chunk("metric", "指标平台 SDK", "SDK"),
            _chunk("erp", "ERP 采购单", "采购", app_id="erp", domain="采购"),
        ]
    )

    service = KeywordRetrievalService(repository, app_id="middle-platform")

    assert repository.index_filters == [{"app_id": "middle-platform"}]
    assert repository.body_requests == []
    assert list(service._record_indexes) == ["metric"]


def test_optional_domain_and_name_narrow_the_keyword_index_scope():
    repository = InMemoryChunkRepository(
        [
            _chunk("metric", "指标平台 SDK", "SDK"),
            _chunk("approval", "审批流加签", "审批", domain="审批流", name="审批流"),
        ]
    )

    KeywordRetrievalService(
        repository,
        app_id="middle-platform",
        domain="审批流",
        name="审批流",
    )

    assert repository.index_filters == [
        {"app_id": "middle-platform", "domain": "审批流", "name": "审批流"}
    ]


def test_title_match_outranks_keyword_only_match_with_default_weights():
    repository = InMemoryChunkRepository(
        [
            _chunk("title-hit", "指标应用小计", "汇总行, summaryRow"),
            _chunk("keyword-hit", "汇总配置", "指标应用小计, summaryRowFlag"),
        ]
    )
    service = KeywordRetrievalService(repository, app_id="middle-platform")

    results = service.search("指标应用小计", k=2)

    assert [result.chunk_id for result in results] == ["title-hit", "keyword-hit"]
    assert results[0].retrieval_route == "keyword"
    assert results[0].score_type == "fielded_bm25"
    assert results[0].higher_is_better is True
    assert results[0].raw_score > results[1].raw_score


def test_search_fetches_full_bodies_only_for_top_k_ids():
    repository = InMemoryChunkRepository(
        [
            _chunk("best", "SDK getDataV2", "SDK, getDataV2"),
            _chunk("second", "SDK 查询", "SDK"),
        ]
    )
    service = KeywordRetrievalService(repository, app_id="middle-platform")

    results = service.search("SDK getDataV2", k=1)

    assert repository.body_requests == [["best"]]
    assert [result.content for result in results] == ["SDK getDataV2\n这是 best 的完整正文。"]


def test_keyword_search_applies_additional_metadata_filter():
    repository = InMemoryChunkRepository(
        [
            _chunk("faq", "SDK 查询", "SDK, getDataV2", chunk_type="faq"),
            _chunk(
                "requirement",
                "SDK 查询需求",
                "SDK, getDataV2",
                chunk_type="product_requirement",
            ),
        ]
    )
    service = KeywordRetrievalService(repository, app_id="middle-platform")

    results = service.search("SDK 查询", k=5, where={"chunk_type": "faq"})

    assert [result.chunk_id for result in results] == ["faq"]


def test_filtered_keyword_search_does_not_fetch_eligible_ids_from_repository():
    repository = InMemoryChunkRepository(
        [
            _chunk("faq", "审批流管理员转办", "管理员, 转办", chunk_type="faq"),
            _chunk(
                "code",
                "审批流管理员转办实现",
                "adminTransfer",
                chunk_type="code",
            ),
        ]
    )
    service = KeywordRetrievalService(repository, app_id="middle-platform")

    results = service.search(
        "管理员转办",
        where={"chunk_type": "faq"},
    )

    assert repository.chunk_id_requests == []
    assert [item.chunk_id for item in results] == ["faq"]
    assert repository.body_requests == [["faq"]]


def test_keyword_search_can_fall_back_to_repository_filtering():
    repository = InMemoryChunkRepository(
        [_chunk("faq", "SDK 查询", "SDK", chunk_type="faq")]
    )
    service = KeywordRetrievalService(
        repository,
        app_id="middle-platform",
        memory_filter_enabled=False,
    )

    assert service.search("SDK", where={"chunk_type": "faq"})
    assert repository.chunk_id_requests == [
        {
            "$and": [
                {"app_id": "middle-platform"},
                {"chunk_type": "faq"},
            ]
        }
    ]


def test_keyword_scores_are_normalized_inside_the_filtered_candidate_set():
    repository = InMemoryChunkRepository(
        [
            _chunk("faq", "SDK 查询", "unrelated", chunk_type="faq"),
            _chunk(
                "requirement",
                "SDK 查询指标应用数据完整指南",
                "unrelated",
                chunk_type="product_requirement",
            ),
        ]
    )
    service = KeywordRetrievalService(repository, app_id="middle-platform")

    results = service.search(
        "SDK 查询指标应用数据完整指南",
        k=5,
        where={"chunk_type": "faq"},
    )

    assert len(results) == 1
    assert results[0].raw_score == 0.65


def test_keyword_search_returns_empty_when_no_lexical_term_matches():
    repository = InMemoryChunkRepository(
        [_chunk("sdk", "SDK 查询", "SDK, getDataV2")]
    )
    service = KeywordRetrievalService(repository, app_id="middle-platform")

    assert service.search("天气预报", k=5) == []
    assert repository.body_requests == []


def test_keyword_search_uses_llm_rewrite_and_keywords_as_additional_tokens():
    repository = InMemoryChunkRepository(
        [_chunk("summary", "指标应用小计", "summaryRowFlag, summaryRow")]
    )
    service = KeywordRetrievalService(repository, app_id="middle-platform")

    results = service.search(
        "这个咋开",
        k=5,
        additional_queries=["指标应用如何开启小计", "summaryRowFlag summaryRow"],
    )

    assert [result.chunk_id for result in results] == ["summary"]


def test_refresh_rebuilds_keyword_index_after_records_change():
    repository = InMemoryChunkRepository(
        [_chunk("sdk", "SDK 查询", "SDK, getDataV2")]
    )
    service = KeywordRetrievalService(repository, app_id="middle-platform")
    repository.chunks.append(_chunk("sql", "SQL 预览", "getSqlV2, SQL"))

    assert service.search("getSqlV2", k=5) == []

    service.refresh()

    assert [result.chunk_id for result in service.search("getSqlV2", k=5)] == ["sql"]
