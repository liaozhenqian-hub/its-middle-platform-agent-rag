from pathlib import Path

from knowledge.repositories.vector_store_repository import VectorStoreRepository
from knowledge.schemas.documents import KnowledgeChunk, KeywordIndexRecord
from knowledge.config.settings import Settings


class FakeEmbeddings:
    def embed_documents(self, texts):
        return [self._embed(text) for text in texts]

    def embed_query(self, text):
        return self._embed(text)

    @staticmethod
    def _embed(text):
        sdk_score = 1.0 if "SDK" in text or "getDataV2" in text else 0.0
        faq_score = 1.0 if "FAQ" in text or "标准回答" in text else 0.0
        length_score = min(len(text) / 1000.0, 1.0)
        return [sdk_score, faq_score, length_score]


def test_repository_upserts_searches_and_filters(tmp_path: Path):
    repo = VectorStoreRepository(
        persist_directory=tmp_path / "chroma",
        collection_name="test_metric_platform",
        embedding=FakeEmbeddings(),
    )
    repo.reset()

    chunks = [
        KnowledgeChunk(
            chunk_id="mp-faq-sdk",
            content="FAQ 标准回答：SDK 使用 MetricClient.getDataV2 查询指标应用数据。",
            heading="FAQ：SDK 怎么查询指标应用数据？",
            metadata={
                "chunk_id": "mp-faq-sdk",
                "chunk_type": "faq",
                "domain": "指标平台",
                "interface_type": "SDK开放接口",
                "retrieval_priority": "high",
            },
        ),
        KnowledgeChunk(
            chunk_id="mp-feishu",
            content="飞书产品需求说明指标应用支持小计。",
            heading="指标应用支持小计",
            metadata={
                "chunk_id": "mp-feishu",
                "chunk_type": "product_requirement",
                "domain": "指标平台",
                "interface_type": "产品需求",
                "retrieval_priority": "low",
            },
        ),
    ]

    repo.upsert(chunks)
    results = repo.search(
        "SDK getDataV2",
        k=5,
        where={"chunk_type": "faq"},
    )

    assert repo.count() == 2
    assert [result.chunk_id for result in results] == ["mp-faq-sdk"]
    assert results[0].metadata["interface_type"] == "SDK开放接口"


def test_repository_reset_clears_collection(tmp_path: Path):
    repo = VectorStoreRepository(
        persist_directory=tmp_path / "chroma",
        collection_name="test_reset",
        embedding=FakeEmbeddings(),
    )
    repo.upsert(
        [
            KnowledgeChunk(
                chunk_id="one",
                content="SDK 文档",
                heading="SDK",
                metadata={"chunk_id": "one", "chunk_type": "faq", "domain": "指标平台"},
            )
        ]
    )

    assert repo.count() == 1
    repo.reset()
    assert repo.count() == 0


def test_repository_deletes_selected_chunks(tmp_path: Path):
    repo = VectorStoreRepository(
        persist_directory=tmp_path / "chroma",
        collection_name="test_delete_chunks",
        embedding=FakeEmbeddings(),
    )
    repo.upsert(
        [
            KnowledgeChunk("one", "one", "One", {"chunk_id": "one"}),
            KnowledgeChunk("two", "two", "Two", {"chunk_id": "two"}),
        ]
    )

    assert repo.delete(["one"]) == 1
    assert repo.get_chunk_ids() == {"two"}
    assert repo.delete([]) == 0


def test_repository_updates_metadata_without_reembedding(tmp_path: Path):
    class CountingEmbeddings(FakeEmbeddings):
        def __init__(self):
            self.document_calls = 0

        def embed_documents(self, texts):
            self.document_calls += 1
            return super().embed_documents(texts)

    embeddings = CountingEmbeddings()
    repo = VectorStoreRepository(
        persist_directory=tmp_path / "chroma_metadata",
        collection_name="test_metadata_update",
        embedding=embeddings,
    )
    chunk = KnowledgeChunk(
        "stable", "stable content", "Stable", {"chunk_id": "stable", "commit_sha": "old"}
    )
    repo.upsert([chunk])
    calls = embeddings.document_calls

    repo.update_metadata([
        KnowledgeChunk(
            "stable", "stable content", "Stable", {"chunk_id": "stable", "commit_sha": "new"}
        )
    ])

    assert embeddings.document_calls == calls
    assert repo.get_chunks(ids=["stable"])[0].metadata["commit_sha"] == "new"


def test_repository_get_chunks_reads_documents_and_applies_metadata_filter(tmp_path: Path):
    repo = VectorStoreRepository(
        persist_directory=tmp_path / "chroma",
        collection_name="test_get_chunks",
        embedding=FakeEmbeddings(),
    )
    repo.upsert(
        [
            KnowledgeChunk(
                chunk_id="faq",
                content="SDK FAQ content",
                heading="SDK FAQ",
                metadata={
                    "chunk_id": "faq",
                    "heading": "SDK FAQ",
                    "bm25_keywords": "SDK, getDataV2",
                    "chunk_type": "faq",
                    "domain": "metric-platform",
                },
            ),
            KnowledgeChunk(
                chunk_id="requirement",
                content="Product requirement content",
                heading="Product requirement",
                metadata={
                    "chunk_id": "requirement",
                    "heading": "Product requirement",
                    "bm25_keywords": "metric application",
                    "chunk_type": "product_requirement",
                    "domain": "metric-platform",
                },
            ),
        ]
    )

    chunks = repo.get_chunks(
        where={"domain": "metric-platform", "chunk_type": "faq"}
    )

    assert [chunk.chunk_id for chunk in chunks] == ["faq"]
    assert chunks[0].heading == "SDK FAQ"
    assert chunks[0].content == "SDK FAQ content"
    assert chunks[0].metadata["bm25_keywords"] == "SDK, getDataV2"


def test_repository_reads_lightweight_keyword_records_with_chroma_filter(tmp_path: Path):
    repo = VectorStoreRepository(
        persist_directory=tmp_path / "chroma",
        collection_name="test_keyword_records",
        embedding=FakeEmbeddings(),
    )
    repo.upsert(
        [
            KnowledgeChunk(
                chunk_id="faq",
                content="This full body must not be loaded into the keyword index.",
                heading="SDK FAQ",
                metadata={
                    "chunk_id": "faq",
                    "heading": "SDK FAQ",
                    "bm25_keywords": "SDK, getDataV2",
                    "chunk_type": "faq",
                    "domain": "metric-platform",
                },
            ),
            KnowledgeChunk(
                chunk_id="workflow",
                content="Workflow body",
                heading="Workflow",
                metadata={
                    "chunk_id": "workflow",
                    "heading": "Workflow",
                    "bm25_keywords": "approval",
                    "chunk_type": "faq",
                    "domain": "workflow",
                },
            ),
        ]
    )

    records = repo.get_keyword_index_records(
        where={"domain": "metric-platform", "chunk_type": "faq"}
    )

    assert records == [
        KeywordIndexRecord(
            chunk_id="faq",
            heading="SDK FAQ",
            keywords="SDK, getDataV2",
            metadata={
                "chunk_id": "faq",
                "heading": "SDK FAQ",
                "bm25_keywords": "SDK, getDataV2",
                "chunk_type": "faq",
                "domain": "metric-platform",
            },
        )
    ]


def test_repository_filters_ids_then_fetches_only_selected_chunk_bodies(tmp_path: Path):
    repo = VectorStoreRepository(
        persist_directory=tmp_path / "chroma",
        collection_name="test_lazy_bodies",
        embedding=FakeEmbeddings(),
    )
    repo.upsert(
        [
            KnowledgeChunk(
                chunk_id="faq",
                content="FAQ full body",
                heading="SDK FAQ",
                metadata={"chunk_id": "faq", "heading": "SDK FAQ", "chunk_type": "faq"},
            ),
            KnowledgeChunk(
                chunk_id="requirement",
                content="Requirement full body",
                heading="Requirement",
                metadata={
                    "chunk_id": "requirement",
                    "heading": "Requirement",
                    "chunk_type": "product_requirement",
                },
            ),
        ]
    )

    assert repo.get_chunk_ids(where={"chunk_type": "faq"}) == {"faq"}

    chunks = repo.get_chunks(ids=["faq"])

    assert [chunk.chunk_id for chunk in chunks] == ["faq"]
    assert chunks[0].content == "FAQ full body"


def test_from_settings_passes_embedding_dimensions(monkeypatch, tmp_path: Path):
    captured = {}

    class CapturingEmbeddings:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(
        "knowledge.repositories.vector_store_repository.OpenAIEmbeddings",
        CapturingEmbeddings,
    )

    settings = Settings(
        EMBEDDING_API_KEY="embedding-key",
        EMBEDDING_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1",
        EMBEDDING_MODEL="text-embedding-v4",
        EMBEDDING_DIMENSIONS=1024,
        EMBEDDING_BATCH_SIZE=10,
        VECTOR_STORE_PATH=tmp_path / "chroma",
        CHROMA_COLLECTION_NAME="test_dimensions",
    )

    VectorStoreRepository.from_settings(settings)

    assert captured["model"] == "text-embedding-v4"
    assert captured["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert captured["dimensions"] == 1024
    assert captured["chunk_size"] == 10
    assert captured["check_embedding_ctx_length"] is False


def test_upsert_logs_delete_failure_and_continues(caplog):
    class DeleteFailingVectorStore:
        def delete(self, ids):
            raise RuntimeError("delete unavailable")

        def add_texts(self, texts, metadatas, ids):
            return ids

    repo = object.__new__(VectorStoreRepository)
    repo.collection_name = "test_logging"
    repo.vector_store = DeleteFailingVectorStore()
    chunks = [
        KnowledgeChunk(
            chunk_id="sdk",
            content="SDK content",
            heading="SDK",
            metadata={"chunk_id": "sdk"},
        )
    ]

    with caplog.at_level(
        "WARNING",
        logger="knowledge.repositories.vector_store_repository",
    ):
        stored_ids = repo.upsert(chunks)

    assert stored_ids == ["sdk"]
    assert "Failed to delete existing vectors" in caplog.text
    assert "collection=test_logging" in caplog.text
    assert "id_count=1" in caplog.text
    assert "delete unavailable" in caplog.text


def test_upsert_respects_chroma_max_batch_size():
    class LimitedClient:
        @staticmethod
        def get_max_batch_size():
            return 2

    class LimitedVectorStore:
        def __init__(self):
            self._client = LimitedClient()
            self.deleted_batches = []
            self.added_batches = []

        def delete(self, ids):
            assert len(ids) <= 2
            self.deleted_batches.append(list(ids))

        def add_texts(self, texts, metadatas, ids):
            assert len(ids) <= 2
            self.added_batches.append(list(ids))
            return list(ids)

    repo = object.__new__(VectorStoreRepository)
    repo.collection_name = "test_batching"
    repo.vector_store = LimitedVectorStore()
    chunks = [
        KnowledgeChunk(
            chunk_id=f"chunk-{index}",
            content=f"content-{index}",
            heading=f"Chunk {index}",
            metadata={"chunk_id": f"chunk-{index}"},
        )
        for index in range(5)
    ]

    stored_ids = repo.upsert(chunks)

    assert stored_ids == [f"chunk-{index}" for index in range(5)]
    assert repo.vector_store.deleted_batches == [
        ["chunk-0", "chunk-1"],
        ["chunk-2", "chunk-3"],
        ["chunk-4"],
    ]
    assert repo.vector_store.added_batches == repo.vector_store.deleted_batches


def test_repository_paginates_keyword_metadata_and_deduplicates_records():
    class PagedCollection:
        def __init__(self):
            self.calls = []
            self.pages = {
                0: {
                    "ids": ["a", "b"],
                    "metadatas": [
                        {"heading": "A", "bm25_keywords": "alpha"},
                        {"heading": "B", "bm25_keywords": "beta"},
                    ],
                },
                2: {
                    "ids": ["b", "c"],
                    "metadatas": [
                        {"heading": "B duplicate", "bm25_keywords": "duplicate"},
                        {
                            "chunk_id": "custom-c",
                            "heading": "C",
                            "bm25_keywords": "gamma",
                        },
                    ],
                },
                4: {
                    "ids": ["c"],
                    "metadatas": [
                        {"heading": "C duplicate", "bm25_keywords": "duplicate"}
                    ],
                },
            }

        def get(self, **kwargs):
            self.calls.append(kwargs)
            return self.pages.get(kwargs.get("offset", 0), {"ids": [], "metadatas": []})

    class PagedVectorStore:
        def __init__(self):
            self._collection = PagedCollection()

    repo = object.__new__(VectorStoreRepository)
    repo.vector_store = PagedVectorStore()
    repo._metadata_page_size = 2

    records = repo.get_keyword_index_records(where={"app_id": "middle-platform"})

    assert repo.vector_store._collection.calls == [
        {
            "include": ["metadatas"],
            "where": {"app_id": "middle-platform"},
            "limit": 2,
            "offset": 0,
        },
        {
            "include": ["metadatas"],
            "where": {"app_id": "middle-platform"},
            "limit": 2,
            "offset": 2,
        },
        {
            "include": ["metadatas"],
            "where": {"app_id": "middle-platform"},
            "limit": 2,
            "offset": 4,
        },
    ]
    assert [record.chunk_id for record in records] == ["a", "b", "custom-c"]
    assert [record.heading for record in records] == ["A", "B", "C"]
