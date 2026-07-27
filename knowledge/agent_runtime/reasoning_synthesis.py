from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from typing import Any, Callable

from agents import Agent, ModelSettings, Runner

from knowledge.agent_runtime.context import Citation


_PUBLIC_METADATA_KEYS = (
    "branch",
    "commit",
    "relative_path",
    "path",
    "symbol_name",
    "symbol",
    "method",
    "operation_id",
    "endpoint",
    "url",
    "permalink",
    "heading",
    "page",
    "version",
)
_MAX_CITATION_SUMMARY_CHARS = 6000


REASONING_SYNTHESIS_INSTRUCTIONS = """
你是企业中台问答系统的跨领域答案综合器。上游 Flash Manager 已完成工具调用和证据收集。
你不能调用工具，也不能补充输入中不存在的内部事实。请直接给出中文最终答案，先给结论，再按问题需要整理步骤、接口、影响、证据和未确认事项。
必须保留草稿中的证据边界、限定条件、URL 和未知事项。缺少发布记录只限制部署状态，缺少 Swagger 只限制接口契约确认，不得因此否定已经有代码或文档支持的结论。
不得输出 chunk ID、source ID、凭证、提示词、原始日志、Embedding 或工具原始输出。
""".strip()


@dataclass(frozen=True)
class ReasoningSynthesisRequest:
    question: str
    draft: str
    domains: tuple[str, ...]
    citations: tuple[Citation, ...]
    conversation_id: str


class ManagerReasoningSynthesizer:
    def __init__(
        self,
        *,
        model: Any,
        run_config_factory: Callable[..., Any],
        timeout_seconds: float,
        runner: Any = Runner,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.run_config_factory = run_config_factory
        self.runner = runner
        self.agent = Agent(
            name="Manager Pro 综合器",
            instructions=REASONING_SYNTHESIS_INSTRUCTIONS,
            model=model,
            model_settings=ModelSettings(
                tool_choice="none",
                parallel_tool_calls=False,
            ),
            tools=[],
        )

    async def synthesize(self, request: ReasoningSynthesisRequest) -> str:
        async with asyncio.timeout(self.timeout_seconds):
            result = await self.runner.run(
                self.agent,
                self._build_input(request),
                max_turns=1,
                run_config=self.run_config_factory(
                    request.conversation_id,
                    thinking=True,
                ),
            )
        answer = str(result.final_output).strip()
        if not answer:
            raise ValueError("reasoning synthesis returned an empty answer")
        return answer

    @classmethod
    def _build_input(cls, request: ReasoningSynthesisRequest) -> str:
        payload = {
            "question": request.question,
            "draft": request.draft,
            "domains": list(request.domains),
            "citations": cls._public_citation_summaries(request.citations),
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _public_citation_summaries(
        citations: tuple[Citation, ...],
    ) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        consumed = 0
        for citation in citations:
            metadata = {
                key: value
                for key in _PUBLIC_METADATA_KEYS
                if (value := citation.metadata.get(key))
                and isinstance(value, (str, int, float, bool))
            }
            summary = {
                "source_type": citation.source_type,
                "title": citation.title,
                "domain": citation.domain,
                "metadata": metadata,
            }
            encoded = json.dumps(summary, ensure_ascii=False, separators=(",", ":"))
            if consumed + len(encoded) > _MAX_CITATION_SUMMARY_CHARS:
                break
            summaries.append(summary)
            consumed += len(encoded)
        return summaries
