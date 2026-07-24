from datetime import UTC, datetime
from pathlib import Path

import pytest

from knowledge.catalog.models import (
    KnowledgeSourceCreate,
    SourceDomainRuleCreate,
    SourceFileCreate,
    SourceType,
    SourceVersionCreate,
)
from knowledge.catalog.repository import CatalogRepository
from knowledge.source_sync.processors import (
    DeleteSourceJobProcessor,
    DocumentSourceJobProcessor,
    SourceJobRouter,
)


class FakeCoordinator:
    def __init__(self):
        self.document_calls = []
        self.deleted = []

    async def index_document_version(self, source_id, version, path):
        self.document_calls.append((source_id, version, Path(path)))

    async def delete_source_content(self, source_id):
        self.deleted.append(source_id)
        return 3


class FakeGitProcessor:
    def __init__(self):
        self.jobs = []

    async def process(self, job):
        self.jobs.append(job.id)


class FakeSecretStore:
    def __init__(self):
        self.deleted = []

    async def delete_all(self, source_id):
        self.deleted.append(source_id)
        return 1


@pytest.mark.asyncio
async def test_document_processor_indexes_registered_path_and_clears_pending_config(tmp_path):
    catalog = CatalogRepository(tmp_path / "catalog.db")
    await catalog.initialize()
    upload_path = tmp_path / "storage" / "uploads" / "doc-1" / "v1"
    upload_path.mkdir(parents=True)
    await catalog.create_source(
        KnowledgeSourceCreate(
            id="doc-1",
            space_id="middle-platform",
            domain_id="metric-platform",
            source_type=SourceType.DOCUMENT,
            name="文档",
            config={
                "pending_upload_path": str(upload_path),
                "pending_version": "v1",
            },
        )
    )
    job = await catalog.enqueue_job(source_id="doc-1", kind="document")
    coordinator = FakeCoordinator()

    await DocumentSourceJobProcessor(catalog, coordinator).process(job)

    assert coordinator.document_calls == [("doc-1", "v1", upload_path)]
    source = await catalog.get_source("doc-1")
    assert "pending_upload_path" not in source.config
    assert "pending_version" not in source.config


@pytest.mark.asyncio
async def test_delete_processor_cleans_only_source_storage_and_soft_deletes_record(tmp_path):
    catalog = CatalogRepository(tmp_path / "catalog.db")
    await catalog.initialize()
    await catalog.create_source(
        KnowledgeSourceCreate(
            id="source-1",
            space_id="middle-platform",
            domain_id=None,
            source_type=SourceType.GIT,
            name="代码",
            config={},
            enabled=False,
        )
    )
    storage = tmp_path / "storage"
    mirror = storage / "git" / "mirrors" / "source-1.git"
    upload = storage / "uploads" / "source-1"
    unrelated = storage / "uploads" / "other"
    mirror.mkdir(parents=True)
    upload.mkdir(parents=True)
    unrelated.mkdir(parents=True)
    job = await catalog.enqueue_job(source_id="source-1", kind="delete")
    coordinator = FakeCoordinator()
    secrets = FakeSecretStore()

    await DeleteSourceJobProcessor(
        catalog, coordinator, storage, secret_store=secrets
    ).process(job)

    assert coordinator.deleted == ["source-1"]
    assert not mirror.exists()
    assert not upload.exists()
    assert unrelated.exists()
    source = await catalog.get_source("source-1")
    assert source.enabled is False
    assert source.config["lifecycle_state"] == "deleted"
    assert secrets.deleted == ["source-1"]


@pytest.mark.asyncio
async def test_delete_processor_purges_catalog_content_without_decryption_key(tmp_path):
    catalog = CatalogRepository(tmp_path / "catalog.db")
    await catalog.initialize()
    await catalog.create_source(
        KnowledgeSourceCreate(
            id="source-1",
            space_id="middle-platform",
            domain_id=None,
            source_type=SourceType.GIT,
            name="代码",
            config={},
            enabled=False,
        )
    )
    await catalog.create_domain_rule(
        SourceDomainRuleCreate(
            id="rule-1",
            source_id="source-1",
            pattern="metric/**",
            target_domain_id="metric-platform",
        )
    )
    await catalog.create_version(
        SourceVersionCreate(
            id="version-1",
            source_id="source-1",
            version_ref="abc123",
            status="succeeded",
            current=True,
        )
    )
    await catalog.create_file(
        SourceFileCreate(
            id="file-1",
            source_id="source-1",
            version_id="version-1",
            relative_path="metric/App.java",
            domain_key="metric-platform",
            language="java",
            content_hash="hash",
            size_bytes=12,
        )
    )
    await catalog.put_swagger_cache(
        "source-1",
        specification={"openapi": "3.0.0"},
        etag="etag",
        last_modified=None,
        refreshed_at=datetime.now(UTC),
    )
    await catalog._set_encrypted_secret("source-1", "bearer", "ciphertext")
    job = await catalog.enqueue_job(source_id="source-1", kind="delete")

    await DeleteSourceJobProcessor(
        catalog,
        FakeCoordinator(),
        tmp_path / "storage",
        secret_store=None,
    ).process(job)

    assert await catalog.list_versions("source-1") == []
    assert await catalog.list_files("source-1") == []
    assert await catalog.list_domain_rules("source-1") == []
    assert await catalog.get_swagger_cache("source-1") is None
    assert await catalog._get_encrypted_secret("source-1", "bearer") is None


@pytest.mark.asyncio
async def test_source_job_router_dispatches_git_document_and_delete(tmp_path):
    catalog = CatalogRepository(tmp_path / "catalog.db")
    await catalog.initialize()
    upload = tmp_path / "upload"
    upload.mkdir()
    await catalog.create_source(
        KnowledgeSourceCreate(
            id="git-1",
            space_id="middle-platform",
            domain_id=None,
            source_type=SourceType.GIT,
            name="代码",
            config={},
        )
    )
    await catalog.create_source(
        KnowledgeSourceCreate(
            id="doc-1",
            space_id="middle-platform",
            domain_id="metric-platform",
            source_type=SourceType.DOCUMENT,
            name="文档",
            config={"pending_upload_path": str(upload), "pending_version": "v1"},
        )
    )
    git_job = await catalog.enqueue_job(source_id="git-1", kind="manual")
    doc_job = await catalog.enqueue_job(source_id="doc-1", kind="document")
    coordinator = FakeCoordinator()
    git = FakeGitProcessor()
    router = SourceJobRouter(
        catalog,
        git_processor=git,
        document_processor=DocumentSourceJobProcessor(catalog, coordinator),
        delete_processor=DeleteSourceJobProcessor(catalog, coordinator, tmp_path / "storage"),
    )

    await router.process(git_job)
    await router.process(doc_job)

    assert git.jobs == [git_job.id]
    assert coordinator.document_calls[0][0] == "doc-1"
