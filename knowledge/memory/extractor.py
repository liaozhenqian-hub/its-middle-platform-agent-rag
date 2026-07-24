from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from knowledge.memory.models import MemoryScope, MemoryType
from knowledge.memory.policy import MemoryPolicy


class ExtractedMemory(BaseModel):
    model_config = ConfigDict(extra="ignore")

    memory_type: MemoryType
    scope_type: MemoryScope = "user"
    subject: str = Field(min_length=1, max_length=100)
    normalized_fact: str = Field(min_length=1, max_length=1000)
    summary: str = Field(min_length=1, max_length=1000)
    confidence: float = Field(ge=0, le=1)


class MemoryExtractionPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    memories: list[ExtractedMemory] = Field(default_factory=list, max_length=10)


class MemoryExtractor:
    def __init__(self, *, client: Any, model: str, policy: MemoryPolicy | None = None):
        self.client = client
        self.model = model
        self.policy = policy or MemoryPolicy()

    async def extract(
        self, question: str, answer: str | None, domain: str | None
    ) -> list[ExtractedMemory]:
        prompt = self._prompt(question, answer or "", domain or "")
        raw = await asyncio.to_thread(self._call, prompt)
        try:
            payload = MemoryExtractionPayload.model_validate_json(raw)
        except Exception as first_error:
            repair = (
                "只修复上一条 JSON，不增加任何记忆。校验错误："
                + type(first_error).__name__
                + "\n原始 JSON："
                + raw[:8000]
            )
            try:
                payload = MemoryExtractionPayload.model_validate_json(
                    await asyncio.to_thread(self._call, repair)
                )
            except Exception:
                return []
        return [
            item for item in payload.memories
            if self.policy.allows_candidate(item.normalized_fact, item.summary)
        ]

    def _call(self, prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            response_format={"type": "json_object"},
            extra_body={"thinking": {"type": "disabled"}},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是长期记忆候选提取器。只提取用户明确表达且未来可能复用的偏好、"
                        "用户上下文、已解决事件或已确认决策。禁止保存密码、令牌、日志正文、"
                        "代码正文、Embedding 和未经用户确认的企业事实。只输出 JSON。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        )
        return str(response.choices[0].message.content or "")

    @staticmethod
    def _prompt(question: str, answer: str, domain: str) -> str:
        return json.dumps(
            {
                "question": question[:4000],
                "answer": answer[:4000],
                "domain": domain[:100],
                "output": {
                    "memory_type": "user_preference|user_context|episodic_memory|decision_memory|procedural_memory",
                    "scope_type": "user|conversation|team|domain",
                    "subject": "stable key",
                    "normalized_fact": "short fact",
                    "summary": "short reusable summary",
                    "confidence": 0.0,
                },
            },
            ensure_ascii=False,
        )
