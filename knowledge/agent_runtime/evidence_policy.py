from __future__ import annotations

from enum import Enum
import re
from typing import Iterable

from knowledge.agent_runtime.context import Citation


class EvidenceLevel(str, Enum):
    CONFIRMED = "confirmed"
    INFERRED = "inferred"
    NOT_FOUND = "not_found"


class EvidencePolicy:
    """Deterministically prevents retrieval misses becoming capability facts."""

    _CAPABILITY_TERM = (
        r"(?:能力|功能|机制|节点|接口|工作流|版本|转换|汇聚|重试)"
    )
    _NEGATIVE_VERB = (
        r"(?:没有|不存在|不支持|"
        r"无法(?:实现|执行|使用|支持|进行)|"
        r"不能(?:实现|执行|使用|支持|进行))"
    )
    _NEGATIVE_CAPABILITY_PATTERN = re.compile(
        rf"(?:{_NEGATIVE_VERB}[^。！？\n]{{0,40}}{_CAPABILITY_TERM}|"
        rf"{_CAPABILITY_TERM}[^。！？\n]{{0,40}}{_NEGATIVE_VERB})"
    )
    _SAFEGUARD = "本次检索暂未找到该能力的明确实现，不能据此确认系统不支持。"

    def classify(self, citations: Iterable[Citation]) -> EvidenceLevel:
        items = list(citations)
        if not items:
            return EvidenceLevel.NOT_FOUND
        for citation in items:
            metadata = citation.metadata
            if metadata.get("explicit_limitation") is True:
                return EvidenceLevel.CONFIRMED
            symbol_type = str(
                metadata.get("symbol_kind") or metadata.get("symbol_type") or ""
            ).casefold()
            if citation.source_type == "code" and symbol_type in {
                "method",
                "function",
                "class",
                "interface",
            }:
                return EvidenceLevel.CONFIRMED
        return EvidenceLevel.INFERRED

    def safeguard(self, answer: str, citations: Iterable[Citation]) -> str:
        evidence_level = self.classify(citations)
        if evidence_level == EvidenceLevel.CONFIRMED:
            answer = re.sub(
                rf"^\s*{re.escape(self._SAFEGUARD)}\s*(?:\n+\s*)?"
                r"(?:以下内容仅作为待验证线索：)?",
                "",
                answer,
            ).lstrip()
        if not self._NEGATIVE_CAPABILITY_PATTERN.search(answer):
            return answer
        if evidence_level == EvidenceLevel.CONFIRMED:
            return answer
        return f"{self._SAFEGUARD}\n\n以下内容仅作为待验证线索：{answer}"
