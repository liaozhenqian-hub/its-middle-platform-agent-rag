import pytest

from knowledge.memory.repository import MemoryRepository
from knowledge.memory.summarizer import ConversationSummarizer


@pytest.mark.asyncio
async def test_conversation_summary_is_separate_and_bounded(tmp_path):
    repository = MemoryRepository(tmp_path / "memory.db")
    await repository.initialize()
    summary = ConversationSummarizer(max_chars=300).build(
        conversation_id="conversation-1",
        user_id="user-1",
        space_id="middle-platform",
        domain_id="approval-flow",
        goals=["完成审批流接口对接"],
        confirmed_facts=["用户已确认使用 develop 分支"],
        unresolved_items=["部署状态尚未确认"],
        preferences=["接口说明包含入参与出参"],
    )

    await repository.upsert_conversation_summary(summary)
    loaded = await repository.get_conversation_summary("conversation-1")

    assert loaded.goals == ("完成审批流接口对接",)
    assert loaded.preferences == ("接口说明包含入参与出参",)
    assert len(loaded.summary) <= 300
    assert await repository.list_memories(owner_id="user-1") == []
