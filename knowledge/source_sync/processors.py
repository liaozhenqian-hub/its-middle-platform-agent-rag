from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime
from pathlib import Path

from knowledge.catalog.models import SourceType, SyncJob
from knowledge.catalog.repository import CatalogRepository


class SourceJobUnavailableError(RuntimeError):
    pass


class DocumentSourceJobProcessor:
    def __init__(self, catalog: CatalogRepository, index_coordinator):
        self.catalog = catalog
        self.index_coordinator = index_coordinator

    async def process(self, job: SyncJob) -> None:
        source = await self.catalog.get_source(job.source_id)
        if (
            source is None
            or not source.enabled
            or source.source_type is not SourceType.DOCUMENT
        ):
            raise SourceJobUnavailableError("document source is unavailable")
        version = str(source.config.get("pending_version") or "").strip()
        upload_path = str(source.config.get("pending_upload_path") or "").strip()
        if not version or not upload_path:
            raise ValueError("document source has no pending upload version")
        await self.index_coordinator.index_document_version(
            source.id,
            version,
            Path(upload_path),
        )
        refreshed = await self.catalog.get_source(source.id)
        assert refreshed is not None
        config = dict(refreshed.config)
        config.pop("pending_version", None)
        config.pop("pending_upload_path", None)
        await self.catalog.update_source(source.id, config=config)


class DeleteSourceJobProcessor:
    def __init__(
        self,
        catalog: CatalogRepository,
        index_coordinator,
        storage_root: str | Path,
        secret_store=None,
    ):
        self.catalog = catalog
        self.index_coordinator = index_coordinator
        self.storage_root = Path(storage_root).resolve()
        self.secret_store = secret_store

    async def process(self, job: SyncJob) -> None:
        source = await self.catalog.get_source(job.source_id)
        if source is None:
            raise SourceJobUnavailableError("source is unavailable")
        await self.index_coordinator.delete_source_content(source.id)
        if self.secret_store is not None:
            await self.secret_store.delete_all(source.id)
        await self.catalog.purge_source_content_records(source.id)
        for candidate in (
            self.storage_root / "git" / "mirrors" / f"{source.id}.git",
            self.storage_root / "uploads" / source.id,
        ):
            target = candidate.resolve()
            if self.storage_root not in target.parents:
                raise ValueError("source storage path is outside the storage root")
            if target.exists():
                await asyncio.to_thread(shutil.rmtree, target)
        await self.catalog.update_source(
            source.id,
            enabled=False,
            config={
                **source.config,
                "lifecycle_state": "deleted",
                "deleted_at": datetime.now(UTC).isoformat(),
            },
        )


class SourceJobRouter:
    def __init__(
        self,
        catalog: CatalogRepository,
        *,
        git_processor,
        document_processor: DocumentSourceJobProcessor,
        delete_processor: DeleteSourceJobProcessor,
    ):
        self.catalog = catalog
        self.git_processor = git_processor
        self.document_processor = document_processor
        self.delete_processor = delete_processor

    async def process(self, job: SyncJob) -> None:
        if job.kind == "delete":
            await self.delete_processor.process(job)
            return
        source = await self.catalog.get_source(job.source_id)
        if source is None:
            raise SourceJobUnavailableError("source is unavailable")
        if source.source_type is SourceType.GIT:
            await self.git_processor.process(job)
            return
        if source.source_type is SourceType.DOCUMENT:
            await self.document_processor.process(job)
            return
        raise SourceJobUnavailableError("source type does not support sync jobs")
