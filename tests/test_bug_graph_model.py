from types import SimpleNamespace

import pytest

from knowledge.bug_graph.model import AgentsBugModelAdapter


class FakeRunner:
    calls = []

    @classmethod
    async def run(cls, agent, prompt, **kwargs):
        cls.calls.append((agent, prompt, kwargs))
        return SimpleNamespace(final_output='{"normalized_problem":"接口失败"}')


class FakeStreamedResult:
    final_output = "问题摘要"

    async def stream_events(self):
        yield SimpleNamespace(
            type="raw_response_event",
            data=SimpleNamespace(
                type="response.output_text.delta",
                delta="问题",
            ),
        )
        yield SimpleNamespace(
            type="raw_response_event",
            data=SimpleNamespace(
                type="response.output_text.delta",
                delta="摘要",
            ),
        )


class FakeStreamingRunner(FakeRunner):
    @classmethod
    def run_streamed(cls, agent, prompt, **kwargs):
        cls.calls.append((agent, prompt, kwargs))
        return FakeStreamedResult()


@pytest.mark.asyncio
async def test_bug_model_adapter_uses_no_tool_agent_for_intake_and_diagnosis():
    FakeRunner.calls = []
    adapter = AgentsBugModelAdapter(
        model="fake-model",
        diagnosis_model="reasoning-model",
        runner=FakeRunner,
        run_config_factory=lambda conversation_id: f"config:{conversation_id}",
        diagnosis_run_config_factory=(
            lambda conversation_id: f"reasoning-config:{conversation_id}"
        ),
        conversation_id="conversation-1",
    )

    intake = await adapter.normalize("用户问题")
    diagnosis = await adapter.generate(
        {"normalized_problem": "接口失败", "evidence_grade": "log_only"},
        {"logs": [], "code": []},
    )

    assert intake == '{"normalized_problem":"接口失败"}'
    assert diagnosis == '{"normalized_problem":"接口失败"}'
    assert len(FakeRunner.calls) == 2
    assert all(call[0].tools == [] for call in FakeRunner.calls)
    assert all(call[2]["max_turns"] == 2 for call in FakeRunner.calls)
    assert FakeRunner.calls[0][0].model == "fake-model"
    assert FakeRunner.calls[1][0].model == "reasoning-model"
    assert FakeRunner.calls[0][2]["run_config"] == "config:conversation-1"
    assert FakeRunner.calls[1][2]["run_config"] == "reasoning-config:conversation-1"
    assert "工具" not in FakeRunner.calls[0][1]


@pytest.mark.asyncio
async def test_bug_model_adapter_binds_run_config_to_current_chat():
    FakeRunner.calls = []
    adapter = AgentsBugModelAdapter(
        model="fake-model",
        runner=FakeRunner,
        run_config_factory=lambda conversation_id: f"config:{conversation_id}",
        conversation_id="fallback",
    )

    with adapter.bind_conversation("conversation-live"):
        await adapter.normalize("用户问题")

    assert FakeRunner.calls[0][2]["run_config"] == "config:conversation-live"


@pytest.mark.asyncio
async def test_bug_model_adapter_streams_diagnosis_model_deltas():
    FakeStreamingRunner.calls = []
    adapter = AgentsBugModelAdapter(
        model="fake-model",
        diagnosis_model="reasoning-model",
        runner=FakeStreamingRunner,
        conversation_id="conversation-stream",
    )
    deltas: list[str] = []

    answer = await adapter.generate_stream(
        {"normalized_problem": "接口失败", "evidence_grade": "log_only"},
        {"logs": [], "code": []},
        deltas.append,
    )

    assert deltas == ["问题", "摘要"]
    assert answer == "问题摘要"
