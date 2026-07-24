from __future__ import annotations

import asyncio
import logging
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from knowledge.catalog import (
    CatalogRepository,
    KnowledgeSourceCreate,
    SourceType,
    SyncJobState,
)


async def _create_git_source(
    catalog: CatalogRepository,
    *,
    source_id: str = "git-1",
    enabled: bool = True,
    config: dict | None = None,
):
    return await catalog.create_source(
        KnowledgeSourceCreate(
            id=source_id,
            space_id="middle-platform",
            domain_id=None,
            source_type=SourceType.GIT,
            name=source_id,
            config=config
            or {
                "project_id": 42,
                "project_url": "https://gitlab.example/platform/backend.git",
                "branch": "main",
            },
            enabled=enabled,
        )
    )


class RecordingProcessor:
    def __init__(self):
        self.jobs = []

    async def process(self, job):
        self.jobs.append(job)


@pytest.mark.asyncio
async def test_worker_claims_and_completes_one_job_with_lease_cas(tmp_path: Path):
    from knowledge.source_sync import SourceSyncWorker

    catalog = CatalogRepository(tmp_path / "catalog.db")
    await catalog.initialize()
    await _create_git_source(catalog)
    now = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)
    queued = await catalog.enqueue_job(source_id="git-1", kind="manual", now=now)
    processor = RecordingProcessor()
    worker = SourceSyncWorker(catalog, processor, worker_id="worker-1")

    assert await worker.run_once(now=now) is True
    assert await worker.run_once(now=now) is False

    completed = await catalog.get_job(queued.id)
    assert completed.state is SyncJobState.SUCCEEDED
    assert completed.worker_id == "worker-1"
    assert completed.attempt == 1
    assert [job.id for job in processor.jobs] == [queued.id]


@pytest.mark.asyncio
async def test_worker_retries_at_5_30_120_seconds_then_fails_with_type_only(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
):
    from knowledge.source_sync import SourceSyncWorker

    class SecretFailure(RuntimeError):
        pass

    class FailingProcessor:
        def __init__(self):
            self.attempts = []

        async def process(self, job):
            self.attempts.append(job.attempt)
            raise SecretFailure("private-token and provider response must not be logged")

    catalog = CatalogRepository(tmp_path / "catalog.db")
    await catalog.initialize()
    await _create_git_source(catalog)
    start = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)
    queued = await catalog.enqueue_job(source_id="git-1", kind="manual", now=start)
    processor = FailingProcessor()
    worker = SourceSyncWorker(catalog, processor, worker_id="worker-1")

    with caplog.at_level(logging.WARNING, logger="knowledge.source_sync.worker"):
        assert await worker.run_once(now=start) is True
        first_retry = await catalog.get_job(queued.id)
        assert first_retry.state is SyncJobState.QUEUED
        assert first_retry.attempt == 1
        assert first_retry.available_at == start + timedelta(seconds=5)
        assert await worker.run_once(now=start + timedelta(seconds=4)) is False

        assert await worker.run_once(now=start + timedelta(seconds=5)) is True
        second_retry = await catalog.get_job(queued.id)
        assert second_retry.state is SyncJobState.QUEUED
        assert second_retry.attempt == 2
        assert second_retry.available_at == start + timedelta(seconds=35)

        assert await worker.run_once(now=start + timedelta(seconds=35)) is True
        third_retry = await catalog.get_job(queued.id)
        assert third_retry.state is SyncJobState.QUEUED
        assert third_retry.attempt == 3
        assert third_retry.available_at == start + timedelta(seconds=155)

        assert await worker.run_once(now=start + timedelta(seconds=155)) is True

    failed = await catalog.get_job(queued.id)
    assert failed.state is SyncJobState.FAILED
    assert failed.attempt == 4
    assert failed.error == "SecretFailure"
    assert processor.attempts == [1, 2, 3, 4]
    assert "SecretFailure" in caplog.text
    assert "private-token" not in caplog.text
    assert "provider response" not in caplog.text


@pytest.mark.asyncio
async def test_worker_start_recovers_stale_lease_and_stop_requeues_cancelled_job(
    tmp_path: Path,
):
    from knowledge.source_sync import SourceSyncWorker

    class BlockingProcessor:
        def __init__(self):
            self.started = asyncio.Event()
            self.cancelled = asyncio.Event()

        async def process(self, job):
            self.started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise

    catalog = CatalogRepository(tmp_path / "catalog.db")
    await catalog.initialize()
    await _create_git_source(catalog)
    now = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)
    queued = await catalog.enqueue_job(
        source_id="git-1",
        kind="manual",
        now=now - timedelta(minutes=5),
    )
    stale_claim = await catalog.claim_next_job(
        "dead-worker",
        now=now - timedelta(minutes=5),
    )
    assert stale_claim.id == queued.id
    processor = BlockingProcessor()
    worker = SourceSyncWorker(
        catalog,
        processor,
        worker_id="worker-1",
        poll_seconds=0.01,
        stale_after_seconds=60,
        clock=lambda: now,
    )

    await worker.start()
    await asyncio.wait_for(processor.started.wait(), timeout=1)
    await worker.stop()
    await worker.stop()

    recovered = await catalog.get_job(queued.id)
    assert processor.cancelled.is_set()
    assert recovered.state is SyncJobState.QUEUED
    assert recovered.attempt == 2
    assert recovered.worker_id is None
    assert recovered.available_at == now


@pytest.mark.asyncio
async def test_periodic_scan_compensates_changed_git_sources_and_isolates_failures(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
):
    from knowledge.source_sync import GitLabBranch, SourceSyncWorker

    class FakeGitLabClient:
        def __init__(self):
            self.calls = []

        async def get_branch(self, project_id, branch):
            self.calls.append((project_id, branch))
            if project_id == 44:
                raise RuntimeError("private GitLab response and token")
            commits = {42: "new-commit", 43: "same-commit"}
            return GitLabBranch(branch, commits[project_id])

    catalog = CatalogRepository(tmp_path / "catalog.db")
    await catalog.initialize()
    await _create_git_source(
        catalog,
        source_id="changed",
        config={
            "project_id": 42,
            "project_url": "https://gitlab.example/changed.git",
            "branch": "main",
            "last_synced_commit": "old-commit",
        },
    )
    await _create_git_source(
        catalog,
        source_id="current",
        config={
            "project_id": 43,
            "project_url": "https://gitlab.example/current.git",
            "branch": "develop",
            "last_synced_commit": "same-commit",
        },
    )
    await _create_git_source(
        catalog,
        source_id="failing",
        config={
            "project_id": 44,
            "project_url": "https://gitlab.example/failing.git",
            "branch": "main",
        },
    )
    await _create_git_source(
        catalog,
        source_id="misconfigured",
        config={"project_id": 45, "project_url": "https://gitlab.example/missing.git"},
    )
    await _create_git_source(
        catalog,
        source_id="disabled",
        enabled=False,
        config={
            "project_id": 46,
            "project_url": "https://gitlab.example/disabled.git",
            "branch": "main",
        },
    )
    gitlab = FakeGitLabClient()
    worker = SourceSyncWorker(
        catalog,
        RecordingProcessor(),
        worker_id="worker-1",
        gitlab_client=gitlab,
    )
    now = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)

    with caplog.at_level(logging.WARNING, logger="knowledge.source_sync.worker"):
        assert await worker.scan_once(now=now) == 1
        assert await worker.scan_once(now=now) == 1

    jobs = await catalog.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].source_id == "changed"
    assert jobs[0].kind == "scheduled"
    assert jobs[0].target_commit == "new-commit"
    assert gitlab.calls == [
        (42, "main"),
        (43, "develop"),
        (44, "main"),
        (42, "main"),
        (43, "develop"),
        (44, "main"),
    ]
    assert "RuntimeError" in caplog.text
    assert "failing" in caplog.text
    assert "private GitLab response" not in caplog.text
    assert "token" not in caplog.text.lower()


@pytest.mark.asyncio
async def test_worker_loop_runs_periodic_scan_at_configured_interval(tmp_path: Path):
    from knowledge.source_sync import GitLabBranch, SourceSyncWorker

    class SignallingGitLabClient:
        def __init__(self):
            self.called = asyncio.Event()

        async def get_branch(self, project_id, branch):
            self.called.set()
            return GitLabBranch(branch, "same-commit")

    catalog = CatalogRepository(tmp_path / "catalog.db")
    await catalog.initialize()
    await _create_git_source(
        catalog,
        config={
            "project_id": 42,
            "project_url": "https://gitlab.example/current.git",
            "branch": "main",
            "last_synced_commit": "same-commit",
        },
    )
    gitlab = SignallingGitLabClient()
    worker = SourceSyncWorker(
        catalog,
        RecordingProcessor(),
        worker_id="worker-1",
        gitlab_client=gitlab,
        poll_seconds=1,
        scan_interval_seconds=0.02,
    )

    await worker.start()
    try:
        await asyncio.wait_for(gitlab.called.wait(), timeout=1)
    finally:
        await worker.stop()

    assert gitlab.called.is_set()


@pytest.mark.asyncio
async def test_git_job_processor_uses_catalog_config_and_cleans_up_after_index_failure(
    tmp_path: Path,
):
    from knowledge.source_sync import GitSnapshot, GitSourceJobProcessor

    main_thread = threading.get_ident()
    snapshot = GitSnapshot(
        commit_sha="new-commit",
        mirror_path=tmp_path / "mirror.git",
        worktree_path=tmp_path / "worktree",
        full_reconcile=False,
        changes=(),
    )

    class RecordingManager:
        def __init__(self):
            self.prepare_kwargs = None
            self.prepare_thread = None
            self.cleanup_snapshot = None
            self.cleanup_thread = None

        def prepare_snapshot(self, **kwargs):
            self.prepare_kwargs = kwargs
            self.prepare_thread = threading.get_ident()
            return snapshot

        def cleanup(self, value):
            self.cleanup_snapshot = value
            self.cleanup_thread = threading.get_ident()

    class FailingCoordinator:
        def __init__(self):
            self.calls = []

        async def index_git_snapshot(self, source_id, value):
            self.calls.append((source_id, value))
            raise RuntimeError("index output must remain private")

    catalog = CatalogRepository(tmp_path / "catalog.db")
    await catalog.initialize()
    await _create_git_source(
        catalog,
        config={
            "project_id": 42,
            "project_url": "https://gitlab.example/platform/backend.git",
            "branch": "release/v1",
            "last_synced_commit": "old-commit",
        },
    )
    now = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)
    queued = await catalog.enqueue_job(
        source_id="git-1",
        kind="webhook",
        target_commit="untrusted-webhook-commit",
        now=now,
    )
    job = await catalog.claim_next_job("worker-1", now=now)
    assert job.id == queued.id
    manager = RecordingManager()
    coordinator = FailingCoordinator()
    processor = GitSourceJobProcessor(catalog, manager, coordinator)

    with pytest.raises(RuntimeError, match="index output"):
        await processor.process(job)

    assert manager.prepare_kwargs == {
        "source_id": "git-1",
        "job_id": job.id,
        "project_url": "https://gitlab.example/platform/backend.git",
        "branch": "release/v1",
        "previous_commit": "old-commit",
    }
    assert coordinator.calls == [("git-1", snapshot)]
    assert manager.cleanup_snapshot is snapshot
    assert manager.prepare_thread != main_thread
    assert manager.cleanup_thread != main_thread
