from __future__ import annotations

import asyncio
import logging
from time import monotonic
from uuid import uuid4

from knowledge.memory.repository import MemoryRepository
from knowledge.memory.service import MemoryService
from knowledge.memory.summarizer import ConversationSummaryService


logger = logging.getLogger(__name__)


class MemoryExtractionWorker:
    def __init__(
        self,
        *,
        repository: MemoryRepository,
        memory_service: MemoryService,
        poll_seconds: float = 2.0,
        stale_seconds: int = 300,
        max_attempts: int = 3,
        summary_service: ConversationSummaryService | None = None,
        maintenance_seconds: float = 60.0,
    ):
        self.repository = repository
        self.memory_service = memory_service
        self.poll_seconds = max(0.01, poll_seconds)
        self.stale_seconds = max(0, stale_seconds)
        self.max_attempts = max(1, max_attempts)
        self.summary_service = summary_service
        self.maintenance_seconds = max(0.01, maintenance_seconds)
        self._last_maintenance_at = 0.0
        self.worker_id = f"memory-worker-{uuid4()}"
        self._task: asyncio.Task | None = None
        self._closing = asyncio.Event()
        self._active_job_id: str | None = None

    async def start(self) -> None:
        await self.repository.recover_stale_extraction_jobs(self.stale_seconds)
        await self._run_maintenance()
        if self._task is None:
            self._closing.clear()
            self._task = asyncio.create_task(self._run(), name=self.worker_id)

    async def close(self) -> None:
        self._closing.set()
        task = self._task
        self._task = None
        active_job_id = self._active_job_id
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if active_job_id is not None:
            await self.repository.requeue_extraction_job(
                active_job_id,
                worker_id=self.worker_id,
            )

    async def _run(self) -> None:
        while not self._closing.is_set():
            if monotonic() - self._last_maintenance_at >= self.maintenance_seconds:
                await self._run_maintenance()
            job = await self.repository.claim_extraction_job(self.worker_id)
            if job is None:
                await asyncio.sleep(self.poll_seconds)
                continue
            self._active_job_id = job.id
            try:
                await self.memory_service.extract_candidates(
                    question=job.question,
                    answer=job.answer,
                    user_id=job.user_id,
                    space_id=job.space_id,
                    domain_id=job.domain_id,
                    source_turn_id=job.source_turn_id,
                    source_citations=job.source_citations,
                )
                if self.summary_service is not None:
                    await self.summary_service.update_from_turn(
                        conversation_id=job.conversation_id,
                        user_id=job.user_id,
                        space_id=job.space_id,
                        domain_id=job.domain_id,
                        question=job.question,
                        answer=job.answer,
                    )
                await self.repository.complete_extraction_job(job.id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "Memory extraction job failed job_id=%s error_type=%s",
                    job.id,
                    type(exc).__name__,
                )
                await self.repository.fail_extraction_job(
                    job.id,
                    error_type=type(exc).__name__,
                    max_attempts=self.max_attempts,
                )
            finally:
                self._active_job_id = None

    async def _run_maintenance(self) -> None:
        try:
            confirmed = await self.memory_service.auto_confirm_due_candidates()
            await self.memory_service.repair_memory_index()
            if confirmed:
                logger.info("Auto-confirmed due user memories count=%s", len(confirmed))
        except Exception as exc:
            logger.warning(
                "Memory maintenance failed error_type=%s",
                type(exc).__name__,
            )
        finally:
            self._last_maintenance_at = monotonic()
