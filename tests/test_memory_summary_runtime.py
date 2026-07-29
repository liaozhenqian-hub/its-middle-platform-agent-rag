import asyncio

import pytest

from knowledge.memory.repository import MemoryRepository
from knowledge.memory.service import MemoryService
from knowledge.memory.summarizer import ConversationSummaryService
from knowledge.memory.worker import MemoryExtractionWorker


@pytest.mark.asyncio
async def test_memory_worker_updates_bounded_summary_and_runtime_augments_next_turn(tmp_path):
    repository = MemoryRepository(tmp_path / "memory.db")
    await repository.initialize()
    job = await repository.enqueue_extraction(
        user_id="user-1",
        conversation_id="conversation-1",
        space_id="middle-platform",
        domain_id="approval-flow",
        channel="web",
        question="管理员转办接口需要哪些参数",
        answer="接口需要 processInstanceId 和 operatorId。",
        source_turn_id="turn-1",
        source_citations=("code-1",),
    )

    class FakeMemoryService:
        async def extract_candidates(self, **kwargs):
            return []

    worker = MemoryExtractionWorker(
        repository=repository,
        memory_service=FakeMemoryService(),
        summary_service=ConversationSummaryService(repository, max_chars=420),
        poll_seconds=0.01,
    )
    await worker.start()
    for _ in range(100):
        if (await repository.get_extraction_job(job.id)).status == "succeeded":
            break
        await asyncio.sleep(0.01)
    await worker.close()

    summary = await repository.get_conversation_summary("conversation-1")
    assert summary is not None
    assert "管理员转办接口需要哪些参数" in summary.summary
    assert "processInstanceId" in summary.summary
    assert len(summary.summary) <= 420

    service = MemoryService(repository)
    augmented = await service.augment_message(
        "上次接口还缺什么",
        user_id="user-1",
        conversation_id="conversation-1",
        space_id="middle-platform",
        domain_id="approval-flow",
    )
    assert "历史会话摘要" in augmented
    assert "管理员转办接口需要哪些参数" in augmented
    assert "严禁在最终回答中复述" in augmented
    assert augmented.endswith("当前问题：\n上次接口还缺什么")
