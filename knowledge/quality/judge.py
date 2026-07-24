from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError


class SemanticJudgeError(RuntimeError):
    pass


class SemanticJudgeResult(BaseModel):
    score: float = Field(ge=0, le=100)
    relevance: float = Field(ge=0, le=100)
    factual_correctness: float = Field(ge=0, le=100)
    citation_support: float = Field(ge=0, le=100)
    unknown_calibration: float = Field(ge=0, le=100)
    actionability: float = Field(ge=0, le=100)
    facts_supported: bool
    critical_contradiction: bool
    reasons: list[str] = Field(default_factory=list, max_length=10)


class DeepSeekSemanticJudge:
    """Tool-free semantic judge with one structured-output repair attempt."""

    def __init__(self, *, client: Any, model: str):
        self.client = client
        self.model = model

    async def judge(
        self,
        *,
        question: str,
        answer: str,
        evidence: list[dict[str, Any]],
        required_facts: list[str],
        forbidden_facts: list[str],
    ) -> dict[str, Any]:
        payload = {
            "question": question[:20_000],
            "answer": answer[:30_000],
            "evidence": evidence[:10],
            "required_facts": required_facts[:100],
            "forbidden_facts": forbidden_facts[:100],
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "你是企业知识问答质量裁判。只根据提供的脱敏证据评分，不调用工具，"
                    "不补充外部知识。输出 JSON：score、relevance、factual_correctness、"
                    "citation_support、unknown_calibration、actionability（0-100），"
                    "facts_supported、critical_contradiction 和 reasons。"
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        validation_error = ""
        for attempt in range(2):
            attempt_messages = list(messages)
            if attempt:
                attempt_messages.append(
                    {
                        "role": "user",
                        "content": f"上次输出未通过 JSON 校验：{validation_error[:1000]}。只返回修复后的 JSON。",
                    }
                )
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=attempt_messages,
                response_format={"type": "json_object"},
                temperature=0,
            )
            raw = str(response.choices[0].message.content or "")
            try:
                return SemanticJudgeResult.model_validate_json(raw).model_dump()
            except (ValidationError, ValueError, json.JSONDecodeError) as exc:
                validation_error = str(exc)
        raise SemanticJudgeError("semantic judge returned invalid JSON twice")
