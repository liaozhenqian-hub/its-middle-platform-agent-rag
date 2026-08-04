import json
from datetime import UTC, datetime

import pytest
from agents.tool_context import ToolContext

from knowledge.agent_runtime.context import AgentRunContext
from knowledge.memory.models import Memory
from knowledge.memory.tools import create_memory_tools


class FakeMemoryService:
    def __init__(self):
        self.calls = []

    async def recall(self, query, *, user_id, space_id, domain_id, scopes=None):
        self.calls.append((query, user_id, space_id, domain_id, scopes))
        now = datetime.now(UTC)
        return [
            Memory(
                id="memory-1",
                scope_type="user",
                owner_id=user_id,
                space_id=space_id,
                domain_id=domain_id,
                memory_type="user_preference",
                subject="answer-format",
                normalized_fact="回答接口问题时包含入参与出参",
                summary="用户偏好接口回答包含入参与出参",
                source_turn_id="turn-1",
                source_citations=("chunk-1",),
                confidence=0.9,
                status="confirmed",
                valid_from=now,
                valid_until=None,
                last_used_at=None,
                supersedes_id=None,
                created_at=now,
                updated_at=now,
            )
        ]


@pytest.mark.asyncio
async def test_memory_tools_use_server_scoped_identity_only():
    service = FakeMemoryService()
    user_tool, domain_tool = create_memory_tools(service)
    context = AgentRunContext(
        conversation_id="conversation-1",
        run_id="run-1",
        user_id="user-1",
        knowledge_space_id="middle-platform",
        domain_id="approval-flow",
    )
    tool_context = ToolContext(
        context=context,
        tool_name=user_tool.name,
        tool_call_id="call-1",
        tool_arguments='{"query":"接口怎么对接"}',
    )

    output = await user_tool.on_invoke_tool(
        tool_context, '{"query":"接口怎么对接"}'
    )

    assert service.calls == [
        (
            "接口怎么对接",
            "user-1",
            "middle-platform",
            "approval-flow",
            ("user",),
        )
    ]
    assert json.loads(output)["memories"][0]["summary"] == "用户偏好接口回答包含入参与出参"
    assert set(user_tool.params_json_schema["properties"]) == {"query"}
    assert set(domain_tool.params_json_schema["properties"]) == {"query"}


@pytest.mark.asyncio
async def test_user_memory_tool_returns_empty_without_authenticated_identity():
    service = FakeMemoryService()
    user_tool, _ = create_memory_tools(service)
    context = AgentRunContext(conversation_id="conversation-1", run_id="run-1")
    tool_context = ToolContext(
        context=context,
        tool_name=user_tool.name,
        tool_call_id="call-1",
        tool_arguments='{"query":"接口怎么对接"}',
    )

    output = await user_tool.on_invoke_tool(
        tool_context, '{"query":"接口怎么对接"}'
    )

    assert json.loads(output) == {"status": "identity_required", "memories": []}
    assert service.calls == []
