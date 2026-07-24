from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from knowledge.api.app import create_app
from knowledge.services.citation_detail_service import (
    CitationDetailNotFound,
    CitationDetailService,
)
from knowledge.schemas.documents import KnowledgeChunk


class FakeCatalog:
    def __init__(self, entries=None, source=None, swagger=None, files=None):
        self.entries = entries or []
        self.source = source
        self.swagger = swagger
        self.files = files or []

    async def list_chunks(self, **kwargs):
        entries = list(self.entries)
        chunk_ids = kwargs.get("chunk_ids")
        if chunk_ids is not None:
            entries = [item for item in entries if item.chunk_id in set(chunk_ids)]
        source_id = kwargs.get("source_id")
        if source_id is not None:
            entries = [item for item in entries if item.source_id == source_id]
        version_id = kwargs.get("version_id")
        if version_id is not None:
            entries = [item for item in entries if item.version_id == version_id]
        return entries

    async def get_source(self, source_id):
        return self.source

    async def get_swagger_cache(self, source_id):
        return self.swagger

    async def list_files(self, source_id, version_id=None, *, file_id=None):
        files = [item for item in self.files if item.source_id == source_id]
        if version_id is not None:
            files = [item for item in files if item.version_id == version_id]
        if file_id is not None:
            files = [item for item in files if item.id == file_id]
        return files


class FakeVectorRepository:
    def __init__(self, chunks=None):
        self.chunks = chunks or []
        self.calls = []

    def get_chunks(self, where=None, ids=None):
        self.calls.append((where, ids))
        if ids is None:
            return list(self.chunks)
        requested = set(ids)
        return [item for item in self.chunks if item.chunk_id in requested]


def _entry(chunk_id, *, heading="接入步骤", part=1):
    return SimpleNamespace(
        chunk_id=chunk_id,
        source_id="source-1",
        version_id="version-id-1",
        source_file_id="file-1",
        metadata={
            "heading": heading,
            "chunk_part": part,
            "relative_path": "docs/guide.md",
            "source_version": "v2",
        },
    )


@pytest.mark.asyncio
async def test_citation_detail_returns_bounded_code_excerpt_and_public_metadata():
    entry = SimpleNamespace(source_id="source-1", chunk_id="code-1")
    chunk = KnowledgeChunk(
        chunk_id="code-1",
        heading="OrderService.create",
        content="x" * 80,
        metadata={
            "source_type": "code",
            "domain_id": "workflow",
            "language": "java",
            "branch": "develop",
            "commit_sha": "abc123",
            "relative_path": "service/OrderService.java",
            "symbol_name": "OrderService.create",
            "start_line": 10,
            "end_line": 20,
            "gitlab_url": "https://gitlab.example/file#L10-20",
            "token": "must-not-leak",
            "content": "must-not-leak",
        },
    )
    service = CitationDetailService(
        catalog=FakeCatalog([entry], source=SimpleNamespace(enabled=True)),
        vector_repository=FakeVectorRepository([chunk]),
        max_chars=50,
    )

    detail = await service.get("code", "code-1")

    assert detail.excerpt == "x" * 50
    assert detail.truncated is True
    assert detail.language == "java"
    assert detail.metadata["branch"] == "develop"
    assert "token" not in detail.metadata
    assert "content" not in detail.metadata


@pytest.mark.asyncio
async def test_product_document_detail_defaults_to_complete_matching_section(tmp_path: Path):
    entries = [_entry("doc-1", part=1), _entry("doc-2", part=2), _entry("other", heading="其他")]
    chunks = [
        KnowledgeChunk("doc-1", "第一部分。", "接入步骤", {"source_type": "product_document", "heading": "接入步骤", "chunk_part": 1, "relative_path": "docs/guide.md", "source_version": "v2"}),
        KnowledgeChunk("doc-2", "第二部分。", "接入步骤", {"source_type": "product_document", "heading": "接入步骤", "chunk_part": 2, "relative_path": "docs/guide.md", "source_version": "v2"}),
        KnowledgeChunk("other", "不应返回。", "其他", {"source_type": "product_document", "heading": "其他", "chunk_part": 1}),
    ]
    service = CitationDetailService(
        catalog=FakeCatalog(
            entries,
            source=SimpleNamespace(enabled=True, domain_id="approval-flow", config={"last_synced_version": "v2"}),
        ),
        vector_repository=FakeVectorRepository(chunks),
        max_chars=10,
        storage_root=tmp_path,
    )

    detail = await service.get("product_document", "doc-1")

    assert detail.excerpt == "第一部分。\n\n第二部分。"
    assert detail.content_scope == "section"
    assert detail.truncated is False
    assert detail.full_text_available is False


@pytest.mark.asyncio
async def test_product_document_full_view_reads_registered_original_and_exposes_url(tmp_path: Path):
    original = tmp_path / "uploads" / "source-1" / "v2" / "docs" / "guide.md"
    original.parent.mkdir(parents=True)
    original.write_text("# 接入步骤\n\n完整正文。\n\n## 参数\n\n`tenantId` 必填。", encoding="utf-8")
    entry = _entry("doc-1")
    source_file = SimpleNamespace(
        id="file-1", source_id="source-1", version_id="version-id-1",
        relative_path="docs/guide.md",
    )
    service = CitationDetailService(
        catalog=FakeCatalog(
            [entry],
            source=SimpleNamespace(enabled=True, domain_id="approval-flow", config={"last_synced_version": "v2"}),
            files=[source_file],
        ),
        vector_repository=FakeVectorRepository([
            KnowledgeChunk("doc-1", "完整正文。", "接入步骤", {"source_type": "product_document"}),
        ]),
        max_chars=10,
        storage_root=tmp_path,
    )

    section = await service.get("product_document", "doc-1")
    detail = await service.get("product_document", "doc-1", view="full")
    path = await service.document_path("doc-1")

    assert detail.excerpt == original.read_text(encoding="utf-8")
    assert detail.content_scope == "full"
    assert detail.truncated is False
    assert section.full_text_available is True
    assert section.document_url == "/api/v1/citations/document?source_id=doc-1"
    assert path == original.resolve()


@pytest.mark.asyncio
async def test_citation_detail_rejects_unknown_or_disabled_chunk():
    service = CitationDetailService(
        catalog=FakeCatalog(),
        vector_repository=FakeVectorRepository(),
        max_chars=6000,
    )
    with pytest.raises(CitationDetailNotFound):
        await service.get("product_document", "missing")

    disabled = CitationDetailService(
        catalog=FakeCatalog(
            [SimpleNamespace(source_id="source-1", chunk_id="doc-1")],
            source=SimpleNamespace(enabled=False),
        ),
        vector_repository=FakeVectorRepository(
            [KnowledgeChunk("doc-1", "body", "heading", {})]
        ),
        max_chars=6000,
    )
    with pytest.raises(CitationDetailNotFound):
        await disabled.get("product_document", "doc-1")


@pytest.mark.asyncio
async def test_citation_detail_resolves_registered_swagger_operation():
    specification = {
        "paths": {
            "/orders/{id}": {
                "get": {
                    "operationId": "getOrder",
                    "summary": "查询订单",
                    "parameters": [{"name": "id", "in": "path"}],
                    "responses": {"200": {"description": "成功"}},
                }
            }
        }
    }
    catalog = FakeCatalog(
        source=SimpleNamespace(enabled=True, domain_id="workflow"),
        swagger={
            "specification": specification,
            "refreshed_at": SimpleNamespace(isoformat=lambda: "2026-07-16T00:00:00+00:00"),
        },
    )
    service = CitationDetailService(
        catalog=catalog,
        vector_repository=FakeVectorRepository(),
        max_chars=6000,
    )

    detail = await service.get("swagger", "swagger-1:getOrder")

    assert detail.title == "getOrder"
    assert "GET /orders/{id}" in detail.excerpt
    assert detail.metadata["method"] == "GET"


@pytest.mark.asyncio
async def test_citation_detail_rejects_mismatched_content_type():
    service = CitationDetailService(
        catalog=FakeCatalog(
            entries=[SimpleNamespace(source_id="source-1", chunk_id="doc-as-code")],
            source=SimpleNamespace(enabled=True, domain_id="workflow"),
        ),
        vector_repository=FakeVectorRepository(
            [
                KnowledgeChunk(
                    chunk_id="doc-as-code",
                    heading="Document",
                    content="document body",
                    metadata={"source_type": "product_document"},
                )
            ]
        ),
        max_chars=6000,
    )

    with pytest.raises(CitationDetailNotFound):
        await service.get("code", "doc-as-code")


def test_citation_detail_api_maps_not_found_and_returns_response():
    class FakeService:
        async def get(self, source_type, source_id, *, view="section"):
            if source_id == "missing":
                raise CitationDetailNotFound(source_id)
            return SimpleNamespace(
                source_type=source_type,
                source_id=source_id,
                title="Title",
                domain="workflow",
                excerpt="excerpt",
                language="java",
                truncated=False,
                metadata={"branch": "develop"},
            )

    app = create_app(
        agent_service=object(),
        component_status={},
        citation_detail_service=FakeService(),
    )
    client = TestClient(app)

    response = client.get(
        "/api/v1/citations/detail",
        params={"source_type": "code", "source_id": "code-1"},
    )
    missing = client.get(
        "/api/v1/citations/detail",
        params={"source_type": "code", "source_id": "missing"},
    )

    assert response.status_code == 200
    assert response.json()["excerpt"] == "excerpt"
    assert missing.status_code == 404
