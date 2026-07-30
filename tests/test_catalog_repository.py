from datetime import UTC, datetime, timedelta

import aiosqlite
import pytest

from knowledge.catalog import (
    CatalogRepository,
    ChunkCatalogCreate,
    CodeSymbolCreate,
    KnowledgeSourceCreate,
    SourceDomainRuleCreate,
    SourceFileCreate,
    SourceType,
    SourceVersionCreate,
    SyncJobState,
)


@pytest.mark.asyncio
async def test_initialize_is_idempotent_enables_sqlite_guards_and_seeds_catalog(tmp_path):
    db_path = tmp_path / "knowledge_catalog.db"
    repository = CatalogRepository(db_path)

    await repository.initialize()
    await repository.initialize()

    async with aiosqlite.connect(db_path) as db:
        migrations = await (
            await db.execute("SELECT version FROM schema_migrations ORDER BY version")
        ).fetchall()
        journal_mode = await (await db.execute("PRAGMA journal_mode")).fetchone()
    assert migrations == [(1,), (2,), (3,), (4,), (5,), (6,)]
    assert journal_mode[0].lower() == "wal"

    spaces = await repository.list_spaces()
    domains = await repository.list_domains(spaces[0].id)
    assert [(space.id, space.name) for space in spaces] == [
        ("middle-platform", "中台")
    ]
    assert [(domain.id, domain.name) for domain in domains] == [
        ("metric-platform", "指标平台"),
        ("approval-flow", "审批流"),
        ("workflow", "工作流"),
    ]

    catalog = await repository.list_spaces_with_domains()
    assert [
        (space.id, [domain.id for domain in space_domains])
        for space, space_domains in catalog
    ] == [
        ("middle-platform", ["metric-platform", "approval-flow", "workflow"])
    ]

    # Foreign keys are enabled on every repository connection, not just migrations.
    with pytest.raises(aiosqlite.IntegrityError):
        await repository.create_source(
            KnowledgeSourceCreate(
                id="bad-source",
                space_id="missing-space",
                domain_id=None,
                source_type=SourceType.GIT,
                name="bad",
                config={},
            )
        )


@pytest.mark.asyncio
async def test_migration_four_replaces_legacy_cross_kind_dedupe_index(tmp_path):
    db_path = tmp_path / "knowledge_catalog.db"
    repository = CatalogRepository(db_path)
    await repository.initialize()

    async with aiosqlite.connect(db_path) as db:
        await db.execute("DROP INDEX idx_sync_jobs_source_kind_commit")
        await db.execute(
            """
            CREATE UNIQUE INDEX idx_sync_jobs_source_commit
            ON sync_jobs(source_id, target_commit)
            WHERE target_commit IS NOT NULL
            """
        )
        await db.execute("DELETE FROM schema_migrations WHERE version=4")
        await db.commit()

    await repository.initialize()
    await repository.initialize()

    async with aiosqlite.connect(db_path) as db:
        indexes = await (await db.execute("PRAGMA index_list(sync_jobs)")).fetchall()
    index_names = {row[1] for row in indexes}
    assert "idx_sync_jobs_source_kind_commit" in index_names
    assert "idx_sync_jobs_source_commit" not in index_names


@pytest.mark.asyncio
async def test_source_crud_rules_and_secret_state_never_expose_ciphertext(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.db")
    await repository.initialize()

    created = await repository.create_source(
        KnowledgeSourceCreate(
            id="source-git",
            space_id="middle-platform",
            domain_id=None,
            source_type=SourceType.GIT,
            name="中台代码",
            config={"project_id": 17, "branch": "main"},
        )
    )
    assert created.credential_configured is False
    assert not hasattr(created, "ciphertext")

    rule = await repository.create_domain_rule(
        SourceDomainRuleCreate(
            id="rule-1",
            source_id=created.id,
            pattern="**/metric/**",
            target_domain_id="metric-platform",
            priority=10,
        )
    )
    shared = await repository.create_domain_rule(
        SourceDomainRuleCreate(
            id="rule-2",
            source_id=created.id,
            pattern="**/*",
            shared=True,
            priority=99,
        )
    )
    assert rule.target_domain_id == "metric-platform"
    assert shared.shared is True

    updated = await repository.update_source(
        created.id,
        name="中台主仓库",
        config={"project_id": 17, "branch": "develop"},
        enabled=False,
    )
    assert updated.name == "中台主仓库"
    assert updated.config["branch"] == "develop"
    assert updated.enabled is False
    assert [item.id for item in await repository.list_domain_rules(created.id)] == [
        "rule-1",
        "rule-2",
    ]

    await repository.delete_source(created.id)
    assert await repository.get_source(created.id) is None
    assert await repository.list_domain_rules(created.id) == []


@pytest.mark.asyncio
async def test_webhook_secret_hash_is_replaceable_and_cascades_with_source(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.db")
    await repository.initialize()
    await repository.create_source(
        KnowledgeSourceCreate(
            id="source-git",
            space_id="middle-platform",
            domain_id=None,
            source_type=SourceType.GIT,
            name="Git source",
            config={},
        )
    )

    assert await repository.get_webhook_secret_hash("source-git") is None
    await repository.set_webhook_secret_hash("source-git", "first-digest")
    assert await repository.get_webhook_secret_hash("source-git") == "first-digest"

    await repository.set_webhook_secret_hash("source-git", "second-digest")
    assert await repository.get_webhook_secret_hash("source-git") == "second-digest"

    await repository.delete_source("source-git")
    assert await repository.get_webhook_secret_hash("source-git") is None


@pytest.mark.asyncio
async def test_versions_files_symbols_and_chunks_are_typed_and_cascade(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.db")
    await repository.initialize()
    await repository.create_source(
        KnowledgeSourceCreate(
            id="source-git",
            space_id="middle-platform",
            domain_id=None,
            source_type=SourceType.GIT,
            name="中台代码",
            config={},
        )
    )

    version = await repository.create_version(
        SourceVersionCreate(
            id="version-1",
            source_id="source-git",
            version_ref="abc123",
            status="ready",
            current=True,
        )
    )
    source_file = await repository.create_file(
        SourceFileCreate(
            id="file-1",
            source_id="source-git",
            version_id=version.id,
            relative_path="metric/MetricService.java",
            domain_key="metric-platform",
            language="java",
            content_hash="sha256:file",
            size_bytes=120,
        )
    )
    symbol = await repository.create_symbol(
        CodeSymbolCreate(
            id="symbol-1",
            source_file_id=source_file.id,
            symbol_type="method",
            name="queryMetric",
            qualified_name="MetricService.queryMetric",
            start_line=12,
            end_line=28,
            metadata={"annotations": ["Override"]},
        )
    )
    chunk = await repository.upsert_chunk(
        ChunkCatalogCreate(
            chunk_id="chunk-1",
            source_id="source-git",
            version_id=version.id,
            source_file_id=source_file.id,
            source_type=SourceType.GIT,
            domain_key="metric-platform",
            locator="MetricService.queryMetric",
            content_hash="sha256:chunk",
            metadata={"heading": "queryMetric"},
        )
    )

    assert (await repository.list_versions("source-git"))[0] == version
    assert (await repository.list_files("source-git", version.id))[0] == source_file
    assert (await repository.list_symbols(source_file.id))[0] == symbol
    assert (await repository.list_chunks(source_id="source-git"))[0] == chunk

    await repository.delete_version(version.id)
    assert await repository.list_versions("source-git") == []
    assert await repository.list_files("source-git") == []
    assert await repository.list_chunks(source_id="source-git") == []


@pytest.mark.asyncio
async def test_version_is_only_promoted_after_indexing_succeeds(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.db")
    await repository.initialize()
    await repository.create_source(
        KnowledgeSourceCreate(
            id="source-doc",
            space_id="middle-platform",
            domain_id="metric-platform",
            source_type=SourceType.DOCUMENT,
            name="产品文档",
            config={},
        )
    )
    await repository.create_version(
        SourceVersionCreate(
            id="version-old",
            source_id="source-doc",
            version_ref="v1",
            status="ready",
            current=True,
        )
    )
    await repository.create_version(
        SourceVersionCreate(
            id="version-new",
            source_id="source-doc",
            version_ref="v2",
            status="indexing",
            current=False,
        )
    )

    promoted = await repository.update_version(
        "version-new",
        status="ready",
        current=True,
        metadata={"chunk_count": 12},
    )

    assert promoted.current is True
    assert promoted.status == "ready"
    assert promoted.metadata == {"chunk_count": 12}
    versions = {item.id: item for item in await repository.list_versions("source-doc")}
    assert versions["version-old"].current is False
    assert versions["version-new"].current is True


@pytest.mark.asyncio
async def test_file_cannot_reference_a_version_owned_by_another_source(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.db")
    await repository.initialize()
    for source_id, domain_id, source_type in (
        ("source-git", None, SourceType.GIT),
        ("source-doc", "metric-platform", SourceType.DOCUMENT),
    ):
        await repository.create_source(
            KnowledgeSourceCreate(
                id=source_id,
                space_id="middle-platform",
                domain_id=domain_id,
                source_type=source_type,
                name=source_id,
                config={},
            )
        )
    await repository.create_version(
        SourceVersionCreate(
            id="git-version",
            source_id="source-git",
            version_ref="abc123",
            status="ready",
        )
    )

    with pytest.raises(aiosqlite.IntegrityError):
        await repository.create_file(
            SourceFileCreate(
                id="cross-source-file",
                source_id="source-doc",
                version_id="git-version",
                relative_path="wrong.md",
                domain_key="metric-platform",
                language="markdown",
                content_hash="sha256:wrong",
                size_bytes=10,
            )
        )


@pytest.mark.asyncio
async def test_sync_jobs_dedupe_claim_complete_fail_and_requeue_stale(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.db")
    await repository.initialize()
    await repository.create_source(
        KnowledgeSourceCreate(
            id="source-git",
            space_id="middle-platform",
            domain_id=None,
            source_type=SourceType.GIT,
            name="中台代码",
            config={},
        )
    )
    now = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)

    first = await repository.enqueue_job(
        source_id="source-git",
        kind="webhook",
        target_commit="abc123",
        now=now,
    )
    duplicate = await repository.enqueue_job(
        source_id="source-git",
        kind="webhook",
        target_commit="abc123",
        now=now,
    )
    assert duplicate.id == first.id

    claimed = await repository.claim_next_job("worker-1", now=now)
    assert claimed is not None
    assert claimed.state is SyncJobState.RUNNING
    assert claimed.attempt == 1
    assert await repository.claim_next_job("worker-2", now=now) is None

    completed = await repository.complete_job(
        claimed.id,
        worker_id=claimed.worker_id,
        attempt=claimed.attempt,
        now=now,
    )
    assert completed.state is SyncJobState.SUCCEEDED

    failed_job = await repository.enqueue_job(
        source_id="source-git", kind="manual", now=now
    )
    failed_job = await repository.claim_next_job("worker-1", now=now)
    failed = await repository.fail_job(
        failed_job.id,
        "network timeout",
        worker_id=failed_job.worker_id,
        attempt=failed_job.attempt,
        now=now,
    )
    assert failed.state is SyncJobState.FAILED
    assert failed.error == "network timeout"

    requeued = await repository.requeue_job(
        failed.id, available_at=now + timedelta(seconds=30), now=now
    )
    assert requeued.state is SyncJobState.QUEUED
    assert await repository.claim_next_job(
        "worker-2", now=now + timedelta(seconds=29)
    ) is None

    claimed_again = await repository.claim_next_job(
        "worker-2", now=now + timedelta(seconds=30)
    )
    assert claimed_again.attempt == 2
    recovered_count = await repository.requeue_stale_jobs(
        stale_before=now + timedelta(seconds=31),
        now=now + timedelta(minutes=1),
    )
    assert recovered_count == 1
    recovered = await repository.get_job(claimed_again.id)
    assert recovered.state is SyncJobState.QUEUED
    assert recovered.worker_id is None


@pytest.mark.asyncio
async def test_stale_worker_cannot_finish_or_requeue_a_newer_claim(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.db")
    await repository.initialize()
    await repository.create_source(
        KnowledgeSourceCreate(
            id="source-git",
            space_id="middle-platform",
            domain_id=None,
            source_type=SourceType.GIT,
            name="中台代码",
            config={},
        )
    )
    now = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)
    job = await repository.enqueue_job(
        source_id="source-git", kind="manual", now=now
    )
    first_claim = await repository.claim_next_job("worker-old", now=now)
    await repository.requeue_stale_jobs(
        stale_before=now + timedelta(seconds=1),
        now=now + timedelta(seconds=2),
    )
    second_claim = await repository.claim_next_job(
        "worker-new", now=now + timedelta(seconds=2)
    )
    assert second_claim.id == job.id
    assert second_claim.attempt == 2

    from knowledge.catalog import CatalogConflictError

    with pytest.raises(CatalogConflictError):
        await repository.complete_job(
            job.id,
            worker_id=first_claim.worker_id,
            attempt=first_claim.attempt,
            now=now + timedelta(seconds=3),
        )
    with pytest.raises(CatalogConflictError):
        await repository.fail_job(
            job.id,
            "late failure",
            worker_id=first_claim.worker_id,
            attempt=first_claim.attempt,
            now=now + timedelta(seconds=3),
        )
    with pytest.raises(CatalogConflictError):
        await repository.requeue_job(
            job.id,
            worker_id=first_claim.worker_id,
            attempt=first_claim.attempt,
            available_at=now + timedelta(seconds=4),
            now=now + timedelta(seconds=3),
        )

    completed = await repository.complete_job(
        job.id,
        worker_id=second_claim.worker_id,
        attempt=second_claim.attempt,
        now=now + timedelta(seconds=3),
    )
    assert completed.state is SyncJobState.SUCCEEDED


@pytest.mark.asyncio
async def test_stale_worker_cannot_requeue_after_newer_attempt_has_failed(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.db")
    await repository.initialize()
    await repository.create_source(
        KnowledgeSourceCreate(
            id="source-git",
            space_id="middle-platform",
            domain_id=None,
            source_type=SourceType.GIT,
            name="中台代码",
            config={},
        )
    )
    now = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)
    job = await repository.enqueue_job(
        source_id="source-git", kind="manual", now=now
    )
    first_claim = await repository.claim_next_job("worker-old", now=now)
    await repository.requeue_stale_jobs(
        stale_before=now + timedelta(seconds=1),
        now=now + timedelta(seconds=2),
    )
    second_claim = await repository.claim_next_job(
        "worker-new", now=now + timedelta(seconds=2)
    )
    await repository.fail_job(
        job.id,
        "new attempt failed",
        worker_id=second_claim.worker_id,
        attempt=second_claim.attempt,
        now=now + timedelta(seconds=3),
    )

    from knowledge.catalog import CatalogConflictError

    with pytest.raises(CatalogConflictError):
        await repository.requeue_job(
            job.id,
            worker_id=first_claim.worker_id,
            attempt=first_claim.attempt,
            available_at=now + timedelta(seconds=4),
            now=now + timedelta(seconds=3),
        )

    requeued = await repository.requeue_job(
        job.id,
        worker_id=second_claim.worker_id,
        attempt=second_claim.attempt,
        available_at=now + timedelta(seconds=4),
        now=now + timedelta(seconds=3),
    )
    assert requeued.state is SyncJobState.QUEUED


@pytest.mark.asyncio
async def test_claim_rejects_blank_worker_id_without_mutating_attempt(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.db")
    await repository.initialize()
    await repository.create_source(
        KnowledgeSourceCreate(
            id="source-git",
            space_id="middle-platform",
            domain_id=None,
            source_type=SourceType.GIT,
            name="中台代码",
            config={},
        )
    )
    now = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)
    job = await repository.enqueue_job(
        source_id="source-git", kind="manual", now=now
    )

    with pytest.raises(ValueError, match="worker_id"):
        await repository.claim_next_job("   ", now=now)

    unchanged = await repository.get_job(job.id)
    assert unchanged.state is SyncJobState.QUEUED
    assert unchanged.attempt == 0
    claimed = await repository.claim_next_job("worker-valid", now=now)
    assert claimed.attempt == 1
    assert claimed.worker_id == "worker-valid"


@pytest.mark.asyncio
async def test_webhook_jobs_require_commit_and_dedupe_within_kind(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.db")
    await repository.initialize()
    await repository.create_source(
        KnowledgeSourceCreate(
            id="source-git",
            space_id="middle-platform",
            domain_id=None,
            source_type=SourceType.GIT,
            name="中台代码",
            config={},
        )
    )
    now = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="target_commit"):
        await repository.enqueue_job(
            source_id="source-git", kind="webhook", now=now
        )
    with pytest.raises(ValueError, match="target_commit"):
        await repository.enqueue_job(
            source_id="source-git",
            kind="webhook",
            target_commit="   ",
            now=now,
        )

    webhook = await repository.enqueue_job(
        source_id="source-git",
        kind="webhook",
        target_commit="abc123",
        now=now,
    )
    duplicate = await repository.enqueue_job(
        source_id="source-git",
        kind="webhook",
        target_commit="abc123",
        now=now,
    )
    manual = await repository.enqueue_job(
        source_id="source-git",
        kind="manual",
        target_commit="abc123",
        now=now,
    )

    assert duplicate.id == webhook.id
    assert manual.id != webhook.id


@pytest.mark.asyncio
async def test_audit_events_are_append_only_and_filterable(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.db")
    await repository.initialize()

    first = await repository.append_audit_event(
        actor="admin",
        action="source.created",
        resource_type="knowledge_source",
        resource_id="source-1",
        details={"source_type": "git"},
    )
    await repository.append_audit_event(
        actor="system",
        action="job.completed",
        resource_type="sync_job",
        resource_id="job-1",
        details={},
    )

    events = await repository.list_audit_events(actor="admin")
    assert [event.id for event in events] == [first.id]
    assert events[0].details == {"source_type": "git"}
