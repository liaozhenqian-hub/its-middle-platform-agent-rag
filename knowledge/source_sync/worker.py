from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Callable, Protocol

from knowledge.catalog.models import SourceType, SyncJob
from knowledge.catalog.repository import CatalogRepository
from knowledge.source_sync.gitlab import GitLabBranch
from knowledge.source_sync.repository import GitSnapshot

logger = logging.getLogger(__name__)
_RETRY_DELAYS_SECONDS = (5, 30, 120)


class JobProcessor(Protocol):
    async def process(self, job: SyncJob) -> None: ...


class GitLabBranchReader(Protocol):
    async def get_branch(
        self,
        project_id: int | str,
        branch_name: str,
    ) -> GitLabBranch: ...


class GitSnapshotManager(Protocol):
    def prepare_snapshot(self, **kwargs) -> GitSnapshot: ...

    def cleanup(self, snapshot: GitSnapshot) -> None: ...


class GitSnapshotIndexer(Protocol):
    async def index_git_snapshot(
        self,
        source_id: str,
        snapshot: GitSnapshot,
    ) -> object: ...


class GitSourceUnavailableError(RuntimeError):
    pass


class GitSourceJobProcessor:
    def __init__(
        self,
        catalog: CatalogRepository,
        repository_manager: GitSnapshotManager,
        index_coordinator: GitSnapshotIndexer,
    ) -> None:
        self.catalog = catalog
        self.repository_manager = repository_manager
        self.index_coordinator = index_coordinator

    async def process(self, job: SyncJob) -> None:
        source = await self.catalog.get_source(job.source_id)
        if source is None or not source.enabled or source.source_type is not SourceType.GIT:
            raise GitSourceUnavailableError("Git source is unavailable")
        project_url = str(source.config.get("project_url") or "").strip()
        branch = str(source.config.get("branch") or "").strip()
        if not project_url or not branch:
            raise ValueError("Git source project_url and branch are required")
        previous_commit = str(
            source.config.get("last_synced_commit") or ""
        ).strip() or None

        snapshot: GitSnapshot | None = None
        try:
            snapshot = await asyncio.to_thread(
                self.repository_manager.prepare_snapshot,
                source_id=source.id,
                job_id=job.id,
                project_url=project_url,
                branch=branch,
                previous_commit=previous_commit,
            )
            await self.index_coordinator.index_git_snapshot(source.id, snapshot)
        finally:
            if snapshot is not None:
                await asyncio.to_thread(self.repository_manager.cleanup, snapshot)


class SourceSyncWorker:
    def __init__(
        self,
        catalog: CatalogRepository,
        processor: JobProcessor,
        *,
        worker_id: str,
        poll_seconds: float = 2.0,
        stale_after_seconds: float = 900.0,
        scan_interval_seconds: float = 600.0,
        clock: Callable[[], datetime] | None = None,
        gitlab_client: GitLabBranchReader | None = None,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id must not be blank")
        if (
            poll_seconds <= 0
            or stale_after_seconds <= 0
            or scan_interval_seconds <= 0
        ):
            raise ValueError("worker timing values must be positive")
        self.catalog = catalog
        self.processor = processor
        self.worker_id = worker_id.strip()
        self.poll_seconds = poll_seconds
        self.stale_after_seconds = stale_after_seconds
        self.scan_interval_seconds = scan_interval_seconds
        self._clock = clock or (lambda: datetime.now(UTC))
        self.gitlab_client = gitlab_client
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        timestamp = self._clock()
        await self.catalog.requeue_stale_jobs(
            stale_before=timestamp - timedelta(seconds=self.stale_after_seconds),
            now=timestamp,
        )
        self._stop_event.clear()
        self._task = asyncio.create_task(
            self._run_loop(),
            name=f"source-sync-worker-{self.worker_id}",
        )

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        self._stop_event.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

    async def _run_loop(self) -> None:
        event_loop = asyncio.get_running_loop()
        next_scan_at = event_loop.time() + self.scan_interval_seconds
        while not self._stop_event.is_set():
            if self.gitlab_client is not None and event_loop.time() >= next_scan_at:
                try:
                    await self.scan_once()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(
                        "Scheduled Git scan loop failed error_type=%s",
                        type(exc).__name__,
                    )
                next_scan_at = event_loop.time() + self.scan_interval_seconds
            try:
                processed = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "Source sync worker loop failed error_type=%s",
                    type(exc).__name__,
                )
                processed = False
            if processed:
                continue
            timeout = self.poll_seconds
            if self.gitlab_client is not None:
                timeout = min(
                    timeout,
                    max(0.0, next_scan_at - event_loop.time()),
                )
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=timeout,
                )
            except TimeoutError:
                pass

    async def run_once(self, now: datetime | None = None) -> bool:
        timestamp = now or self._clock()
        job = await self.catalog.claim_next_job(self.worker_id, now=timestamp)
        if job is None:
            return False
        try:
            await self.processor.process(job)
        except asyncio.CancelledError:
            cancelled_at = now or self._clock()
            await self.catalog.requeue_job(
                job.id,
                worker_id=self.worker_id,
                attempt=job.attempt,
                available_at=cancelled_at,
                now=cancelled_at,
            )
            raise
        except Exception as exc:
            failed_at = now or self._clock()
            error_type = type(exc).__name__
            logger.warning(
                "Source sync job processing failed job_id=%s source_id=%s "
                "attempt=%s error_type=%s",
                job.id,
                job.source_id,
                job.attempt,
                error_type,
            )
            if job.attempt <= len(_RETRY_DELAYS_SECONDS):
                delay = _RETRY_DELAYS_SECONDS[job.attempt - 1]
                await self.catalog.requeue_job(
                    job.id,
                    worker_id=self.worker_id,
                    attempt=job.attempt,
                    available_at=failed_at + timedelta(seconds=delay),
                    now=failed_at,
                )
            else:
                await self.catalog.fail_job(
                    job.id,
                    error_type,
                    worker_id=self.worker_id,
                    attempt=job.attempt,
                    now=failed_at,
                )
            return True
        completed_at = now or self._clock()
        await self.catalog.complete_job(
            job.id,
            worker_id=self.worker_id,
            attempt=job.attempt,
            now=completed_at,
        )
        return True

    async def scan_once(self, now: datetime | None = None) -> int:
        if self.gitlab_client is None:
            return 0
        timestamp = now or self._clock()
        sources = await self.catalog.list_sources(
            source_type=SourceType.GIT,
            enabled=True,
        )
        queued_count = 0
        for source in sources:
            project_id = source.config.get("project_id")
            branch = str(source.config.get("branch") or "").strip()
            if project_id is None or not str(project_id).strip() or not branch:
                continue
            try:
                remote = await self.gitlab_client.get_branch(project_id, branch)
            except Exception as exc:
                logger.warning(
                    "Scheduled Git source scan failed source_id=%s error_type=%s",
                    source.id,
                    type(exc).__name__,
                )
                continue
            remote_commit = remote.commit_sha.strip()
            previous_commit = str(
                source.config.get("last_synced_commit") or ""
            ).strip()
            if not remote_commit or remote_commit == previous_commit:
                continue
            await self.catalog.enqueue_job(
                source_id=source.id,
                kind="scheduled",
                target_commit=remote_commit,
                now=timestamp,
            )
            queued_count += 1
        return queued_count
