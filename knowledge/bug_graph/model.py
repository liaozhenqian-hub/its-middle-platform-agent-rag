from __future__ import annotations

import json
import inspect
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable

from agents import Agent, Runner


class AgentsBugModelAdapter:
    def __init__(
        self,
        *,
        model: Any,
        diagnosis_model: Any | None = None,
        conversation_id: str,
        runner: Any = Runner,
        run_config_factory: Callable[[str], Any] | None = None,
        diagnosis_run_config_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.conversation_id = conversation_id
        self._active_conversation: ContextVar[str | None] = ContextVar(
            "bug_graph_conversation_id",
            default=None,
        )
        self.runner = runner
        self.run_config_factory = run_config_factory
        self.diagnosis_run_config_factory = (
            diagnosis_run_config_factory or run_config_factory
        )
        self.intake_agent = Agent(
            name="Bug Intake Normalizer",
            instructions=(
                "把中文 Bug 描述整理成一个 JSON 对象。只提取原文存在的信息，不推测环境，"
                "不编造 trace ID。字段为 normalized_problem、environment、"
                "environment_evidence、trace_id、service、endpoint、request_time、"
                "request_time_evidence、symptoms、domain_hints。请求时间也只能摘录原文。"
                "只输出 JSON。"
            ),
            model=model,
            tools=[],
        )
        self.diagnosis_agent = Agent(
            name="Bug Diagnosis Writer",
            instructions=(
                "仅依据提供的脱敏日志和代码证据输出中文诊断。区分确认事实、可能原因和未知项，"
                "按问题摘要、日志证据、代码位置、原因与置信度、其他原因、修复方案、"
                "验证步骤、缺失信息组织。log_only 时不得声明代码根因。"
            ),
            model=diagnosis_model or model,
            tools=[],
        )

    @contextmanager
    def bind_conversation(self, conversation_id: str):
        token = self._active_conversation.set(conversation_id)
        try:
            yield
        finally:
            self._active_conversation.reset(token)

    async def normalize(
        self,
        message: str,
        validation_feedback: str | None = None,
    ) -> str:
        prompt = f"用户描述：\n{message}"
        if validation_feedback:
            prompt += f"\n上次输出校验失败：{validation_feedback}"
        return await self._run(
            self.intake_agent,
            prompt,
            run_config_factory=self.run_config_factory,
        )

    async def generate(self, state: dict[str, Any], evidence: dict[str, Any]) -> str:
        return await self._run(
            self.diagnosis_agent,
            json.dumps(self._diagnosis_payload(state, evidence), ensure_ascii=False),
            run_config_factory=self.diagnosis_run_config_factory,
        )

    async def generate_stream(
        self,
        state: dict[str, Any],
        evidence: dict[str, Any],
        on_delta: Callable[[str], Any],
    ) -> str:
        kwargs: dict[str, Any] = {"max_turns": 2}
        if self.diagnosis_run_config_factory is not None:
            conversation_id = self._active_conversation.get() or self.conversation_id
            kwargs["run_config"] = self.diagnosis_run_config_factory(conversation_id)
        streamed = self.runner.run_streamed(
            self.diagnosis_agent,
            json.dumps(self._diagnosis_payload(state, evidence), ensure_ascii=False),
            **kwargs,
        )
        async for event in streamed.stream_events():
            if (
                event.type == "raw_response_event"
                and getattr(event.data, "type", "") == "response.output_text.delta"
            ):
                callback_result = on_delta(str(event.data.delta))
                if inspect.isawaitable(callback_result):
                    await callback_result
        return str(streamed.final_output)

    @staticmethod
    def _diagnosis_payload(
        state: dict[str, Any], evidence: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "problem": state.get("normalized_problem"),
            "environment": state.get("environment"),
            "trace_id": state.get("trace_id"),
            "evidence_grade": state.get("evidence_grade"),
            "exception_types": state.get("exception_types", []),
            "stack_frames": state.get("stack_frames", []),
            "evidence": evidence,
        }

    async def _run(
        self,
        agent: Agent,
        prompt: str,
        *,
        run_config_factory: Callable[[str], Any] | None = None,
    ) -> str:
        kwargs: dict[str, Any] = {"max_turns": 2}
        if run_config_factory is not None:
            conversation_id = self._active_conversation.get() or self.conversation_id
            kwargs["run_config"] = run_config_factory(conversation_id)
        result = await self.runner.run(agent, prompt, **kwargs)
        return str(result.final_output)
