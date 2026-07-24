from pathlib import Path
import hashlib
from types import SimpleNamespace

import pytest

from knowledge.catalog.models import (
    ChunkCatalogCreate,
    KnowledgeSourceCreate,
    SourceDomainRuleCreate,
    SourceFileCreate,
    SourceType,
    SourceVersionCreate,
)
from knowledge.catalog.repository import CatalogRepository
from knowledge.indexing.coordinator import SourceIndexCoordinator
from knowledge.source_sync import GitChangeType, GitFileChange, GitSnapshot
from knowledge.schemas.documents import KnowledgeChunk


class FakeVectorRepository:
    def __init__(self):
        self.events = []
        self.chunks = {}

    def upsert(self, chunks):
        self.events.append(("upsert", [chunk.chunk_id for chunk in chunks]))
        self.chunks.update({chunk.chunk_id: chunk for chunk in chunks})
        return [chunk.chunk_id for chunk in chunks]

    def delete(self, chunk_ids):
        self.events.append(("delete", list(chunk_ids)))
        for chunk_id in chunk_ids:
            self.chunks.pop(chunk_id, None)
        return len(chunk_ids)

    def get_chunks(self, ids=None, where=None):
        selected = ids or list(self.chunks)
        return [self.chunks[item] for item in selected if item in self.chunks]


class FakeRegistry:
    def __init__(self):
        self.invalidations = []

    def invalidate(self, **kwargs):
        self.invalidations.append(kwargs)
        return 1


async def _seed_old_chunk(repository, source_id, source_type):
    await repository.create_version(
        SourceVersionCreate(
            id=f"{source_id}-old-version",
            source_id=source_id,
            version_ref="old",
            status="succeeded",
            current=True,
        )
    )
    await repository.upsert_chunk(
        ChunkCatalogCreate(
            chunk_id=f"{source_id}-old-chunk",
            source_id=source_id,
            version_id=f"{source_id}-old-version",
            source_file_id=None,
            source_type=source_type,
            domain_key="metric-platform",
            locator="old.txt",
            content_hash="old-hash",
            metadata={"relative_path": "old.txt"},
        )
    )


@pytest.mark.asyncio
async def test_git_full_index_upserts_before_deleting_and_promotes_commit(tmp_path: Path):
    catalog = CatalogRepository(tmp_path / "catalog.db")
    await catalog.initialize()
    await catalog.create_source(
        KnowledgeSourceCreate(
            id="git-1",
            space_id="middle-platform",
            domain_id=None,
            source_type=SourceType.GIT,
            name="中台代码",
            config={
                "branch": "main",
                "project_web_url": "https://gitlab.example/platform/middle",
            },
        )
    )
    await catalog.create_domain_rule(
        SourceDomainRuleCreate(
            id="rule-1",
            source_id="git-1",
            pattern="**/metric/**",
            target_domain_id="metric-platform",
            priority=100,
        )
    )
    await _seed_old_chunk(catalog, "git-1", SourceType.GIT)
    worktree = tmp_path / "worktree"
    source_path = worktree / "server" / "metric" / "MetricService.java"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(
        "public class MetricService { public String query() { return \"ok\"; } }",
        encoding="utf-8",
    )
    vector = FakeVectorRepository()
    registry = FakeRegistry()
    coordinator = SourceIndexCoordinator(catalog, vector, registry)

    summary = await coordinator.index_git_snapshot(
        "git-1",
        GitSnapshot(
            commit_sha="abc123",
            mirror_path=tmp_path / "mirror.git",
            worktree_path=worktree,
            full_reconcile=True,
            changes=(),
        ),
    )

    assert summary.upserted >= 1
    assert vector.events[0][0] == "upsert"
    assert vector.events[-1] == ("delete", ["git-1-old-chunk"])
    chunks = await catalog.list_chunks(source_id="git-1")
    assert "git-1-old-chunk" not in {chunk.chunk_id for chunk in chunks}
    assert all(chunk.domain_key == "metric-platform" for chunk in chunks)
    indexed = next(iter(vector.chunks.values()))
    assert indexed.metadata["app_id"] == "middle-platform"
    assert indexed.metadata["source_type"] == "code"
    assert indexed.metadata["commit_sha"] == "abc123"
    assert "/-/blob/abc123/server/metric/MetricService.java" in indexed.metadata[
        "gitlab_url"
    ]
    source = await catalog.get_source("git-1")
    assert source.config["last_synced_commit"] == "abc123"
    assert registry.invalidations == [{"app_id": "middle-platform"}]


def test_git_index_diff_only_embeds_new_or_changed_content():
    old = [
        SimpleNamespace(
            chunk_id="same",
            content_hash=hashlib.sha256("unchanged".encode()).hexdigest(),
        ),
        SimpleNamespace(chunk_id="changed", content_hash="old-hash"),
    ]
    same = KnowledgeChunk(
        "same", "unchanged", "same", {"source_type": "code"}
    )
    changed = KnowledgeChunk(
        "changed", "new content", "changed", {"source_type": "code"}
    )
    new = KnowledgeChunk("new", "new file", "new", {"source_type": "code"})

    to_embed, metadata_only = SourceIndexCoordinator._diff_chunks(old, [same, changed, new])

    assert [chunk.chunk_id for chunk in to_embed] == ["changed", "new"]
    assert [chunk.chunk_id for chunk in metadata_only] == ["same"]


@pytest.mark.asyncio
async def test_document_failure_keeps_old_current_version_and_vectors(tmp_path: Path):
    catalog = CatalogRepository(tmp_path / "catalog.db")
    await catalog.initialize()
    await catalog.create_source(
        KnowledgeSourceCreate(
            id="doc-1",
            space_id="middle-platform",
            domain_id="metric-platform",
            source_type=SourceType.DOCUMENT,
            name="产品文档",
            config={},
        )
    )
    await _seed_old_chunk(catalog, "doc-1", SourceType.DOCUMENT)
    vector = FakeVectorRepository()

    class FailingParser:
        def parse(self, *args, **kwargs):
            raise RuntimeError("parse failed")

    coordinator = SourceIndexCoordinator(
        catalog,
        vector,
        FakeRegistry(),
        document_parser=FailingParser(),
    )
    upload = tmp_path / "upload"
    upload.mkdir()
    (upload / "guide.md").write_text("# 指标\n说明", encoding="utf-8")

    with pytest.raises(RuntimeError, match="parse failed"):
        await coordinator.index_document_version("doc-1", "v2", upload)

    versions = await catalog.list_versions("doc-1")
    current = next(version for version in versions if version.current)
    assert current.version_ref == "old"
    assert vector.events == []
    assert {item.chunk_id for item in await catalog.list_chunks(source_id="doc-1")} == {
        "doc-1-old-chunk"
    }


@pytest.mark.asyncio
async def test_document_version_replaces_old_chunks_after_new_upsert(tmp_path: Path):
    catalog = CatalogRepository(tmp_path / "catalog.db")
    await catalog.initialize()
    await catalog.create_source(
        KnowledgeSourceCreate(
            id="doc-1",
            space_id="middle-platform",
            domain_id="metric-platform",
            source_type=SourceType.DOCUMENT,
            name="产品文档",
            config={},
        )
    )
    await _seed_old_chunk(catalog, "doc-1", SourceType.DOCUMENT)
    vector = FakeVectorRepository()
    registry = FakeRegistry()
    upload = tmp_path / "upload"
    (upload / "docs").mkdir(parents=True)
    (upload / "docs" / "guide.md").write_text("# 指标口径\n销售额定义。", encoding="utf-8")
    coordinator = SourceIndexCoordinator(catalog, vector, registry)

    summary = await coordinator.index_document_version("doc-1", "v2", upload)

    assert summary.upserted == 1
    assert vector.events[0][0] == "upsert"
    assert vector.events[-1] == ("delete", ["doc-1-old-chunk"])
    current = next(
        item for item in await catalog.list_versions("doc-1") if item.current
    )
    assert current.version_ref == "v2"
    document = next(iter(vector.chunks.values()))
    assert document.metadata["relative_path"] == "docs/guide.md"
    assert document.metadata["source_type"] == "product_document"
    source = await catalog.get_source("doc-1")
    assert source.config["last_synced_version"] == "v2"


@pytest.mark.asyncio
async def test_document_missing_root_never_erases_current_knowledge(tmp_path: Path):
    catalog = CatalogRepository(tmp_path / "catalog.db")
    await catalog.initialize()
    await catalog.create_source(
        KnowledgeSourceCreate(
            id="doc-1",
            space_id="middle-platform",
            domain_id="metric-platform",
            source_type=SourceType.DOCUMENT,
            name="产品文档",
            config={},
        )
    )
    await _seed_old_chunk(catalog, "doc-1", SourceType.DOCUMENT)
    vector = FakeVectorRepository()
    vector.chunks["doc-1-old-chunk"] = KnowledgeChunk(
        "doc-1-old-chunk", "旧文档", "旧文档", {"chunk_id": "doc-1-old-chunk"}
    )
    coordinator = SourceIndexCoordinator(catalog, vector, FakeRegistry())

    with pytest.raises(ValueError, match="root"):
        await coordinator.index_document_version("doc-1", "v2", tmp_path / "missing")

    assert vector.events == []
    assert set(vector.chunks) == {"doc-1-old-chunk"}
    assert next(item for item in await catalog.list_versions("doc-1") if item.current).version_ref == "old"


@pytest.mark.asyncio
async def test_post_upsert_promotion_failure_restores_old_vector_and_catalog(tmp_path: Path):
    catalog = CatalogRepository(tmp_path / "catalog.db")
    await catalog.initialize()
    await catalog.create_source(
        KnowledgeSourceCreate(
            id="doc-1",
            space_id="middle-platform",
            domain_id="metric-platform",
            source_type=SourceType.DOCUMENT,
            name="产品文档",
            config={},
        )
    )
    await _seed_old_chunk(catalog, "doc-1", SourceType.DOCUMENT)
    old_chunk = KnowledgeChunk(
        "doc-1-old-chunk",
        "旧文档正文",
        "旧文档",
        {"chunk_id": "doc-1-old-chunk", "relative_path": "old.txt"},
    )
    vector = FakeVectorRepository()
    vector.chunks[old_chunk.chunk_id] = old_chunk
    upload = tmp_path / "upload"
    upload.mkdir()
    (upload / "guide.md").write_text("# 新文档\n新正文", encoding="utf-8")
    coordinator = SourceIndexCoordinator(catalog, vector, FakeRegistry())
    original_update = catalog.update_version
    failed_once = False

    async def fail_promotion(version_id, **kwargs):
        nonlocal failed_once
        if kwargs.get("current") is True and not failed_once:
            failed_once = True
            raise RuntimeError("promotion failed")
        return await original_update(version_id, **kwargs)

    catalog.update_version = fail_promotion

    with pytest.raises(RuntimeError, match="promotion failed"):
        await coordinator.index_document_version("doc-1", "v2", upload)

    assert set(vector.chunks) == {"doc-1-old-chunk"}
    assert vector.chunks["doc-1-old-chunk"].content == "旧文档正文"
    assert {item.chunk_id for item in await catalog.list_chunks(source_id="doc-1")} == {
        "doc-1-old-chunk"
    }
    assert next(item for item in await catalog.list_versions("doc-1") if item.current).version_ref == "old"


@pytest.mark.asyncio
async def test_current_version_retry_repairs_checkpoint_and_invalidates_bm25(tmp_path: Path):
    catalog = CatalogRepository(tmp_path / "catalog.db")
    await catalog.initialize()
    await catalog.create_source(
        KnowledgeSourceCreate(
            id="doc-1",
            space_id="middle-platform",
            domain_id="metric-platform",
            source_type=SourceType.DOCUMENT,
            name="产品文档",
            config={},
        )
    )
    upload = tmp_path / "upload"
    upload.mkdir()
    (upload / "guide.md").write_text("# 文档\n正文", encoding="utf-8")
    vector = FakeVectorRepository()
    registry = FakeRegistry()
    coordinator = SourceIndexCoordinator(catalog, vector, registry)
    await coordinator.index_document_version("doc-1", "v2", upload)
    await catalog.update_source("doc-1", config={})
    registry.invalidations.clear()

    summary = await coordinator.index_document_version("doc-1", "v2", upload)

    assert summary.upserted == 0
    assert (await catalog.get_source("doc-1")).config["last_synced_version"] == "v2"
    assert registry.invalidations == [{"app_id": "middle-platform"}]


@pytest.mark.asyncio
async def test_retry_failure_discards_orphan_failed_candidate_without_restoring_stale_fk(
    tmp_path: Path,
):
    catalog = CatalogRepository(tmp_path / "catalog.db")
    await catalog.initialize()
    await catalog.create_source(
        KnowledgeSourceCreate(
            id="doc-1",
            space_id="middle-platform",
            domain_id="metric-platform",
            source_type=SourceType.DOCUMENT,
            name="产品文档",
            config={},
        )
    )
    await _seed_old_chunk(catalog, "doc-1", SourceType.DOCUMENT)
    await catalog.create_version(
        SourceVersionCreate(
            id="failed-v2",
            source_id="doc-1",
            version_ref="v2",
            status="failed",
            current=False,
        )
    )
    await catalog.create_file(
        SourceFileCreate(
            id="failed-file",
            source_id="doc-1",
            version_id="failed-v2",
            relative_path="guide.md",
            domain_key="metric-platform",
            language=None,
            content_hash="failed",
            size_bytes=1,
        )
    )
    await catalog.upsert_chunk(
        ChunkCatalogCreate(
            chunk_id="failed-orphan",
            source_id="doc-1",
            version_id="failed-v2",
            source_file_id="failed-file",
            source_type=SourceType.DOCUMENT,
            domain_key="metric-platform",
            locator="guide.md#failed",
            content_hash="failed",
            metadata={"relative_path": "guide.md"},
        )
    )

    class FailOnceVector(FakeVectorRepository):
        def __init__(self):
            super().__init__()
            self.fail_once = True

        def upsert(self, chunks):
            if self.fail_once:
                self.fail_once = False
                raise RuntimeError("embedding failed")
            return super().upsert(chunks)

    vector = FailOnceVector()
    vector.chunks["doc-1-old-chunk"] = KnowledgeChunk(
        "doc-1-old-chunk", "旧正文", "旧文档", {"chunk_id": "doc-1-old-chunk"}
    )
    vector.chunks["failed-orphan"] = KnowledgeChunk(
        "failed-orphan", "失败候选", "失败", {"chunk_id": "failed-orphan"}
    )
    upload = tmp_path / "upload"
    upload.mkdir()
    (upload / "guide.md").write_text("# 新文档\n正文", encoding="utf-8")

    with pytest.raises(RuntimeError, match="embedding failed"):
        await SourceIndexCoordinator(catalog, vector, FakeRegistry()).index_document_version(
            "doc-1", "v2", upload
        )

    assert set(vector.chunks) == {"doc-1-old-chunk"}
    assert {item.chunk_id for item in await catalog.list_chunks(source_id="doc-1")} == {
        "doc-1-old-chunk"
    }


@pytest.mark.asyncio
async def test_git_failure_restores_live_chunks_owned_by_older_succeeded_versions(
    tmp_path: Path,
):
    catalog = CatalogRepository(tmp_path / "catalog.db")
    await catalog.initialize()
    await catalog.create_source(
        KnowledgeSourceCreate(
            id="git-1",
            space_id="middle-platform",
            domain_id=None,
            source_type=SourceType.GIT,
            name="代码",
            config={"branch": "main"},
        )
    )
    for version_id, version_ref, current in (
        ("v1", "c1", False),
        ("v2", "c2", True),
    ):
        await catalog.create_version(
            SourceVersionCreate(
                id=version_id,
                source_id="git-1",
                version_ref=version_ref,
                status="succeeded",
                current=current,
            )
        )
    for chunk_id, version_id, path in (
        ("unchanged-v1", "v1", "common/Common.java"),
        ("changed-v2", "v2", "metric/Metric.java"),
    ):
        await catalog.upsert_chunk(
            ChunkCatalogCreate(
                chunk_id=chunk_id,
                source_id="git-1",
                version_id=version_id,
                source_file_id=None,
                source_type=SourceType.GIT,
                domain_key="shared",
                locator=path,
                content_hash=chunk_id,
                metadata={"relative_path": path},
            )
        )

    class FailOnceVector(FakeVectorRepository):
        def __init__(self):
            super().__init__()
            self.fail_once = True

        def upsert(self, chunks):
            if self.fail_once:
                self.fail_once = False
                raise RuntimeError("embedding failed")
            return super().upsert(chunks)

    vector = FailOnceVector()
    for chunk_id in ("unchanged-v1", "changed-v2"):
        vector.chunks[chunk_id] = KnowledgeChunk(
            chunk_id,
            f"{chunk_id} body",
            chunk_id,
            {"chunk_id": chunk_id},
        )
    worktree = tmp_path / "worktree"
    changed_file = worktree / "metric" / "Metric.java"
    changed_file.parent.mkdir(parents=True)
    changed_file.write_text(
        "public class Metric { public void changed() {} }", encoding="utf-8"
    )
    snapshot = GitSnapshot(
        commit_sha="c3",
        mirror_path=tmp_path / "mirror.git",
        worktree_path=worktree,
        full_reconcile=False,
        changes=(GitFileChange(GitChangeType.MODIFIED, "metric/Metric.java"),),
    )

    with pytest.raises(RuntimeError, match="embedding failed"):
        await SourceIndexCoordinator(catalog, vector, FakeRegistry()).index_git_snapshot(
            "git-1", snapshot
        )

    assert set(vector.chunks) == {"unchanged-v1", "changed-v2"}
    assert {item.chunk_id for item in await catalog.list_chunks(source_id="git-1")} == {
        "unchanged-v1",
        "changed-v2",
    }
