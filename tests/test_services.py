from pathlib import Path

import pytest

from knowledge.repositories.vector_store_repository import VectorStoreRepository
from knowledge.services.ingestion_service import IngestionService
from knowledge.services.retrieval_service import RetrievalService


class FakeEmbeddings:
    def embed_documents(self, texts):
        return [self._embed(text) for text in texts]

    def embed_query(self, text):
        return self._embed(text)

    @staticmethod
    def _embed(text):
        return [
            1.0 if "SDK" in text or "getDataV2" in text else 0.0,
            1.0 if "指标应用" in text else 0.0,
            min(len(text) / 1000.0, 1.0),
        ]


def test_ingestion_service_dry_run_uses_markdown_chunks():
    service = IngestionService(
        repository=None,
        source_path=Path(r"D:\javaProgram\metric-platform-knowledge.md"),
        max_chunk_chars=5000,
        overlap_chars=200,
        app_id="middle-platform",
    )

    summary = service.dry_run()

    assert summary.source_path.endswith("metric-platform-knowledge.md")
    assert summary.chunk_count >= 70
    assert summary.parent_chunk_count >= 70


def test_ingestion_and_retrieval_service_roundtrip(tmp_path: Path):
    repo = VectorStoreRepository(
        persist_directory=tmp_path / "chroma",
        collection_name="roundtrip",
        embedding=FakeEmbeddings(),
    )
    service = IngestionService(
        repository=repo,
        source_path=Path(r"D:\javaProgram\metric-platform-knowledge.md"),
        max_chunk_chars=5000,
        overlap_chars=200,
        app_id="middle-platform",
        domain="指标平台",
        name="指标平台",
    )

    summary = service.ingest(reset=True)
    results = RetrievalService(repo).search(
        "SDK 怎么查询指标应用数据？",
        k=3,
        where={"app_id": "middle-platform", "chunk_type": "faq"},
    )

    assert summary.stored_count >= 70
    assert results
    assert any("SDK" in result.content or "getDataV2" in result.content for result in results)


def test_ingestion_service_does_not_reset_existing_vectors_when_reset_flag_is_passed():
    class NonResettingRepository:
        def __init__(self):
            self.reset_called = False
            self.upsert_called = False
            self.stored_chunks = []

        def reset(self):
            self.reset_called = True

        def upsert(self, chunks):
            self.upsert_called = True
            self.stored_chunks = chunks
            return [chunk.chunk_id for chunk in chunks]

        def count(self):
            return 97

    repo = NonResettingRepository()
    service = IngestionService(
        repository=repo,
        source_path=Path(r"D:\javaProgram\metric-platform-knowledge.md"),
        max_chunk_chars=5000,
        overlap_chars=200,
        app_id="middle-platform",
        domain="指标平台",
        name="指标平台",
    )

    summary = service.ingest(reset=True)

    assert repo.reset_called is False
    assert repo.upsert_called is True
    assert summary.stored_count == 97
    assert all(chunk.metadata["app_id"] == "middle-platform" for chunk in repo.stored_chunks)
    assert all(chunk.metadata["domain"] == "指标平台" for chunk in repo.stored_chunks)
    assert all(chunk.metadata["name"] == "指标平台" for chunk in repo.stored_chunks)


def test_ingestion_service_requires_app_id():
    with pytest.raises(ValueError, match="app_id is required"):
        IngestionService(
            repository=None,
            source_path=Path(r"D:\javaProgram\metric-platform-knowledge.md"),
            max_chunk_chars=5000,
            overlap_chars=200,
            app_id="",
        )
