import asyncio

import pytest

from knowledge.memory.policy import MemoryPolicy
from knowledge.memory.repository import MemoryRepository
from knowledge.memory.worker import MemoryExtractionWorker


@pytest.mark.asyncio
async def test_memory_job_queue_recovers_stale_running_and_worker_extracts(tmp_path):
    repository = MemoryRepository(tmp_path / "memory.db")
    await repository.initialize()
    job = await repository.enqueue_extraction(
        user_id="user-1",
        conversation_id="conversation-1",
        space_id="middle-platform",
        domain_id="approval-flow",
        channel="web",
        question="以后接口回答都给我入参和出参",
        answer="好的，后续会按这个格式回答。",
        source_turn_id="turn-1",
        source_citations=("chunk-1",),
    )
    claimed = await repository.claim_extraction_job("worker-old")
    assert claimed.id == job.id
    assert await repository.recover_stale_extraction_jobs(0) == 1

    class FakeMemoryService:
        def __init__(self):
            self.calls = []

        async def extract_candidates(self, **kwargs):
            self.calls.append(kwargs)
            return []

    service = FakeMemoryService()
    worker = MemoryExtractionWorker(
        repository=repository,
        memory_service=service,
        poll_seconds=0.01,
        stale_seconds=60,
    )
    await worker.start()
    for _ in range(100):
        current = await repository.get_extraction_job(job.id)
        if current.status == "succeeded":
            break
        await asyncio.sleep(0.01)
    await worker.close()

    assert (await repository.get_extraction_job(job.id)).status == "succeeded"
    assert service.calls[0]["user_id"] == "user-1"
    assert service.calls[0]["source_turn_id"] == "turn-1"


@pytest.mark.asyncio
async def test_memory_queue_rejects_sensitive_turns(tmp_path):
    repository = MemoryRepository(tmp_path / "memory.db")
    await repository.initialize()

    with pytest.raises(ValueError, match="unsafe"):
        await repository.enqueue_extraction(
            user_id="user-1",
            conversation_id="conversation-1",
            space_id="middle-platform",
            domain_id=None,
            channel="web",
            question="请记住 Authorization: Bearer abc",
            answer="好的",
            source_turn_id="turn-1",
            source_citations=(),
            policy=MemoryPolicy(),
        )


@pytest.mark.asyncio
async def test_memory_worker_requeues_active_job_on_graceful_shutdown(tmp_path):
    repository = MemoryRepository(tmp_path / "memory.db")
    await repository.initialize()
    job = await repository.enqueue_extraction(
        user_id="user-1",
        conversation_id="conversation-1",
        space_id="middle-platform",
        domain_id=None,
        channel="web",
        question="以后回答保持简洁",
        answer="已收到这个偏好",
        source_turn_id="turn-1",
        source_citations=(),
    )
    started = asyncio.Event()

    class BlockingService:
        async def extract_candidates(self, **kwargs):
            started.set()
            await asyncio.Event().wait()

    worker = MemoryExtractionWorker(
        repository=repository,
        memory_service=BlockingService(),
        poll_seconds=0.01,
    )
    await worker.start()
    await asyncio.wait_for(started.wait(), timeout=1)
    await worker.close()

    assert (await repository.get_extraction_job(job.id)).status == "queued"


@pytest.mark.asyncio
async def test_memory_worker_runs_auto_confirmation_maintenance_on_start(tmp_path):
    repository = MemoryRepository(tmp_path / "memory.db")
    await repository.initialize()

    class MaintenanceService:
        def __init__(self):
            self.maintenance_calls = 0

        async def auto_confirm_due_candidates(self):
            self.maintenance_calls += 1
            return []

        async def extract_candidates(self, **kwargs):
            return []

    service = MaintenanceService()
    worker = MemoryExtractionWorker(
        repository=repository,
        memory_service=service,
        poll_seconds=0.01,
        maintenance_seconds=60,
    )

    await worker.start()
    await worker.close()

    assert service.maintenance_calls == 1
