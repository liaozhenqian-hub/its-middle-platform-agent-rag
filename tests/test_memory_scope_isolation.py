import pytest

from knowledge.memory.models import Memory
from knowledge.memory.service import MemoryService


class FakeRepository:
    def __init__(self):
        self.calls = []

    async def list_memories(self, **kwargs):
        self.calls.append(kwargs)
        return []


@pytest.mark.asyncio
async def test_user_memory_tool_scope_does_not_include_domain_memories():
    repository = FakeRepository()
    service = MemoryService(repository)
    await service.recall(
        "偏好", user_id="user-a", space_id="middle-platform", domain_id="approval-flow",
        scopes=("user",),
    )

    assert [call["scope_type"] for call in repository.calls] == ["user"]


@pytest.mark.asyncio
async def test_conversation_summary_scope_requires_matching_domain():
    class SummaryRepository(FakeRepository):
        async def get_conversation_summary(self, conversation_id):
            return type("Summary", (), {
                "user_id": "user-a", "space_id": "middle-platform",
                "domain_id": "approval-flow", "summary": "审批上下文",
            })()

    service = MemoryService(SummaryRepository())
    result = await service.augment_message(
        "指标问题", user_id="user-a", conversation_id="conversation-a",
        space_id="middle-platform", domain_id="metric-platform",
    )

    assert "审批上下文" not in result
