import asyncio
from types import SimpleNamespace

import pytest

from knowledge.agent_runtime.context import Citation
from knowledge.agent_runtime.reasoning_synthesis import (
    ManagerReasoningSynthesizer,
    ReasoningSynthesisRequest,
)


class RecordingRunner:
    def __init__(self, answer: str = "综合后的答案", delay: float = 0):
        self.answer = answer
        self.delay = delay
        self.calls = []

    async def run(self, agent, model_input, **kwargs):
        self.calls.append((agent, model_input, kwargs))
        if self.delay:
            await asyncio.sleep(self.delay)
        return SimpleNamespace(final_output=self.answer)


def _request() -> ReasoningSynthesisRequest:
    return ReasoningSynthesisRequest(
        question="审批通过后如何触发工作流？",
        draft="Flash 草稿",
        domains=("approval-flow", "workflow"),
        citations=(
            Citation(
                source_type="code",
                source_id="private-source-id",
                title="代码：ApprovalService.java / approve",
                domain="审批流",
                metadata={
                    "branch": "develop",
                    "relative_path": "approval/ApprovalService.java",
                    "symbol_name": "approve",
                    "url": "https://git.example/approval/ApprovalService.java#L20",
                    "chunk_id": "private-chunk-id",
                    "token": "private-token",
                    "content": "private source body",
                },
            ),
        ),
        conversation_id="conversation-cross-domain",
    )


@pytest.mark.asyncio
async def test_synthesizer_uses_thinking_run_config_without_tools():
    runner = RecordingRunner()
    run_configs = []

    def make_run_config(conversation_id: str, *, thinking: bool = False):
        run_configs.append((conversation_id, thinking))
        return "thinking-enabled"

    synthesizer = ManagerReasoningSynthesizer(
        model="pro-model",
        run_config_factory=make_run_config,
        timeout_seconds=60,
        runner=runner,
    )

    answer = await synthesizer.synthesize(_request())

    assert answer == "综合后的答案"
    assert synthesizer.agent.tools == []
    assert run_configs == [("conversation-cross-domain", True)]
    assert runner.calls[0][2]["run_config"] == "thinking-enabled"
    assert runner.calls[0][2]["max_turns"] == 1


@pytest.mark.asyncio
async def test_synthesizer_only_includes_public_bounded_citation_metadata():
    runner = RecordingRunner()
    synthesizer = ManagerReasoningSynthesizer(
        model="pro-model",
        run_config_factory=lambda *_args, **_kwargs: None,
        timeout_seconds=60,
        runner=runner,
    )

    await synthesizer.synthesize(_request())

    model_input = runner.calls[0][1]
    assert "审批通过后如何触发工作流" in model_input
    assert "Flash 草稿" in model_input
    assert "ApprovalService.java" in model_input
    assert "develop" in model_input
    assert "private-source-id" not in model_input
    assert "private-chunk-id" not in model_input
    assert "private-token" not in model_input
    assert "private source body" not in model_input


@pytest.mark.asyncio
async def test_synthesizer_rejects_empty_output():
    synthesizer = ManagerReasoningSynthesizer(
        model="pro-model",
        run_config_factory=lambda *_args, **_kwargs: None,
        timeout_seconds=60,
        runner=RecordingRunner(answer="  "),
    )

    with pytest.raises(ValueError, match="empty"):
        await synthesizer.synthesize(_request())


@pytest.mark.asyncio
async def test_synthesizer_times_out():
    synthesizer = ManagerReasoningSynthesizer(
        model="pro-model",
        run_config_factory=lambda *_args, **_kwargs: None,
        timeout_seconds=0.01,
        runner=RecordingRunner(delay=0.05),
    )

    with pytest.raises(TimeoutError):
        await synthesizer.synthesize(_request())
