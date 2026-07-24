from collections.abc import AsyncIterator

import pytest
from agents import Agent, ModelSettings, RunConfig, function_tool
from agents.items import ModelResponse
from agents.models.interface import Model
from agents.usage import Usage
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)

from knowledge.agent_runtime.pending_runs import PendingRunRepository
from knowledge.agent_runtime.service import AgentService
from knowledge.agent_runtime.sessions import AgentSessionFactory


class ApprovalModel(Model):
    def __init__(self):
        self.calls = 0

    async def get_response(self, *args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            output = [
                ResponseFunctionToolCall(
                    arguments='{"value":"approved-value"}',
                    call_id="call-1",
                    name="synthetic_write",
                    type="function_call",
                )
            ]
        else:
            output = [
                ResponseOutputMessage(
                    id="message-1",
                    content=[
                        ResponseOutputText(
                            annotations=[],
                            text="处理完成",
                            type="output_text",
                        )
                    ],
                    role="assistant",
                    status="completed",
                    type="message",
                )
            ]
        return ModelResponse(output=output, usage=Usage(), response_id=None)

    async def stream_response(self, *args, **kwargs) -> AsyncIterator:
        if False:
            yield None


class DisabledTracingFactory:
    def create_run_config(self, conversation_id):
        return RunConfig(
            group_id=conversation_id,
            tracing_disabled=True,
            trace_include_sensitive_data=False,
        )


async def build_service(tmp_path, executed):
    @function_tool(needs_approval=True)
    async def synthetic_write(value: str) -> str:
        """Synthetic write used only to verify approval pause and resume."""
        executed.append(value)
        return "ok"

    manager = Agent(
        name="Manager Agent",
        instructions="Call the write tool, then report the result.",
        model=ApprovalModel(),
        tools=[synthetic_write],
        model_settings=ModelSettings(parallel_tool_calls=False),
    )
    repository = PendingRunRepository(tmp_path / "agent.db")
    await repository.initialize()
    return AgentService(
        manager=manager,
        model_factory=DisabledTracingFactory(),
        session_factory=AgentSessionFactory(tmp_path / "agent.db", 50),
        pending_runs=repository,
    )


@pytest.mark.asyncio
async def test_hitl_approval_persists_then_executes_tool_on_resume(tmp_path):
    executed = []
    service = await build_service(tmp_path, executed)

    paused = await service.chat("执行写操作", "conversation-1")

    assert paused.status == "approval_required"
    assert paused.answer is None
    assert paused.approvals[0].tool_call_id == "call-1"
    assert executed == []

    completed = await service.decide(
        paused.run_id,
        [{"tool_call_id": "call-1", "decision": "approve"}],
    )

    assert completed.status == "completed"
    assert completed.answer == "处理完成"
    assert executed == ["approved-value"]


@pytest.mark.asyncio
async def test_hitl_rejection_resumes_without_executing_tool(tmp_path):
    executed = []
    service = await build_service(tmp_path, executed)
    paused = await service.chat("执行写操作", "conversation-1")

    completed = await service.decide(
        paused.run_id,
        [{"tool_call_id": "call-1", "decision": "reject"}],
    )

    assert completed.status == "completed"
    assert executed == []
