from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any, Callable, Iterable


_INTERNAL_TOKEN = re.compile(
    r"(?i)(?<![\w])(?:chunk|code|doc(?:ument)?|source|swagger)[-_]"
    r"(?=[A-Za-z0-9._:/-]{6,})(?=[A-Za-z0-9._:/-]*\d)[A-Za-z0-9._:/-]+"
)
_EXPLICIT_INTERNAL_REFERENCE = re.compile(
    r"(?i)\b(?:chunk(?:[_ ]?id)?|source(?:[_ ]?id)?)\s*[:：=#-]+\s*"
    r"(?:chunk|code|doc(?:ument)?|source|swagger)[-_]"
    r"[A-Za-z0-9._:/-]{6,}"
)
_STREAM_INTERNAL_SUFFIX = re.compile(
    r"(?i)(?<![A-Za-z0-9_])"
    r"(?:chunk(?:[_ ]?id)?|source(?:[_ ]?id)?|code|doc(?:ument)?|swagger)"
    r"(?:[_ :：=#-][A-Za-z0-9._:/-]*)?$"
)
_INTERNAL_PREFIXES = ("chunk", "source", "code", "doc", "document", "swagger")
INTERNAL_CONTEXT_LEAK_ANSWER = "抱歉，本次回答生成异常，请重新提问。"
_INTERNAL_MEMORY_LINE = re.compile(
    r"(?im)^[ \t]*(?:#{1,6}[ \t]*)?(?:\*\*|__)?(?:"
    r"历史会话摘要|已确认的长期记忆|相关历史上下文|"
    r"\[(?:user_preference|user_context|episodic_memory|"
    r"decision_memory|procedural_memory)\]"
    r")"
)
_INTERNAL_MEMORY_MARKERS = (
    "历史会话摘要",
    "已确认的长期记忆",
    "相关历史上下文",
    "[user_preference]",
    "[user_context]",
    "[episodic_memory]",
    "[decision_memory]",
    "[procedural_memory]",
)


def _possible_memory_prefix_start(text: str) -> int | None:
    line_start = text.rfind("\n") + 1
    candidate = text[line_start:].lstrip(" \t")
    candidate = re.sub(r"^(?:#{1,6}[ \t]*|\*\*|__)", "", candidate)
    normalized = candidate.casefold()
    if not normalized or any(
        marker.casefold().startswith(normalized)
        for marker in _INTERNAL_MEMORY_MARKERS
    ):
        return line_start
    return None


def _value(citation: Any, key: str, default: Any = "") -> Any:
    if isinstance(citation, dict):
        return citation.get(key, default)
    return getattr(citation, key, default)


def _metadata(citation: Any) -> dict[str, Any]:
    value = _value(citation, "metadata", {})
    return value if isinstance(value, dict) else {}


def _basename(value: Any) -> str:
    normalized = str(value or "").strip().replace("\\", "/")
    return PurePosixPath(normalized).name if normalized else ""


def _is_internal_title(title: str, source_id: str) -> bool:
    normalized = title.strip()
    return (
        not normalized
        or normalized == source_id.strip()
        or bool(_INTERNAL_TOKEN.fullmatch(normalized))
    )


def public_citation_name(citation: Any) -> str:
    """Return a user-facing source name without exposing its internal identity."""
    source_type = str(_value(citation, "source_type", "") or "")
    source_id = str(_value(citation, "source_id", "") or "")
    title = str(_value(citation, "title", "") or "").strip()
    metadata = _metadata(citation)
    semantic_title = "" if _is_internal_title(title, source_id) else title

    if source_type == "code":
        filename = _basename(metadata.get("relative_path") or metadata.get("path"))
        symbol = str(
            metadata.get("symbol_name")
            or metadata.get("symbol")
            or metadata.get("method")
            or ""
        ).strip()
        if not symbol and semantic_title and semantic_title != filename:
            symbol = semantic_title
        parts = [item for item in (filename, symbol) if item]
        return f"代码：{' / '.join(dict.fromkeys(parts))}" if parts else "代码位置"

    if source_type == "product_document":
        name = semantic_title or _basename(
            metadata.get("relative_path") or metadata.get("path")
        )
        return f"文档：《{name}》" if name else "产品文档"

    if source_type == "knowledge_chunk":
        name = semantic_title or str(metadata.get("heading") or "").strip()
        return f"知识文档：《{name}》" if name else "知识文档"

    if source_type == "swagger":
        method = str(metadata.get("method") or "").strip().upper()
        path = str(metadata.get("path") or "").strip()
        operation = " ".join(item for item in (method, path) if item)
        operation = operation or semantic_title
        return f"接口：{operation}" if operation else "接口定义"

    if source_type == "log_trace":
        environment = str(metadata.get("environment") or "").strip().casefold()
        environment_name = {
            "develop": "开发环境",
            "dev": "开发环境",
            "test": "测试环境",
            "prod": "生产环境",
            "production": "生产环境",
            "master": "生产环境",
        }.get(environment, "目标环境")
        return f"{environment_name}日志证据"

    if source_type == "mcp_tool":
        return "指标平台查询结果"
    return "检索证据"


def _generic_internal_name(match: re.Match[str]) -> str:
    token = match.group(0).casefold()
    if token.startswith("code-") or token.startswith("code_"):
        return "代码位置"
    if token.startswith("swagger-") or token.startswith("swagger_"):
        return "接口定义"
    return "知识文档"


def sanitize_public_answer(text: str | None, citations: Iterable[Any] = ()) -> str:
    """Remove internal citation identities while preserving readable evidence names."""
    answer = str(text or "")
    memory_match = _INTERNAL_MEMORY_LINE.search(answer)
    if memory_match:
        safe_prefix = answer[: memory_match.start()].rstrip()
        if not safe_prefix:
            return INTERNAL_CONTEXT_LEAK_ANSWER
        answer = safe_prefix
    citation_list = list(citations)
    for citation in sorted(
        citation_list,
        key=lambda item: len(str(_value(item, "source_id", "") or "")),
        reverse=True,
    ):
        source_id = str(_value(citation, "source_id", "") or "").strip()
        if source_id:
            answer = answer.replace(source_id, public_citation_name(citation))
    answer = _EXPLICIT_INTERNAL_REFERENCE.sub("知识文档", answer)
    answer = _INTERNAL_TOKEN.sub(_generic_internal_name, answer)
    answer = re.sub(
        r"(?i)\b(?:chunk|source)[_ ]?id\s*[:：=#-]?\s*(?=知识文档|代码位置|接口定义)",
        "",
        answer,
    )
    return answer


class PublicAnswerStream:
    """Sanitize streamed text while retaining enough tail to catch split IDs."""

    def __init__(
        self,
        citations: Callable[[], Iterable[Any]] | Iterable[Any] = (),
        *,
        tail_chars: int = 128,
    ):
        self._citations = citations
        self._tail_chars = max(32, tail_chars)
        self._buffer = ""
        self._blocked_internal_context = False
        self._emitted_safe_text = False

    def _current_citations(self) -> Iterable[Any]:
        return self._citations() if callable(self._citations) else self._citations

    def feed(self, delta: str) -> str:
        if self._blocked_internal_context:
            return ""
        self._buffer += str(delta or "")
        memory_match = _INTERNAL_MEMORY_LINE.search(self._buffer)
        if memory_match:
            ready = self._buffer[: memory_match.start()].rstrip()
            self._buffer = ""
            self._blocked_internal_context = True
            public = sanitize_public_answer(ready, self._current_citations())
            if public:
                self._emitted_safe_text = True
            return public
        memory_prefix_start = _possible_memory_prefix_start(self._buffer)
        if memory_prefix_start is not None:
            ready, self._buffer = (
                self._buffer[:memory_prefix_start],
                self._buffer[memory_prefix_start:],
            )
            public = sanitize_public_answer(ready, self._current_citations())
            if public:
                self._emitted_safe_text = True
            return public
        match = _STREAM_INTERNAL_SUFFIX.search(self._buffer)
        cut = match.start() if match else len(self._buffer)
        if not match:
            lowered = self._buffer.casefold()
            for length in range(1, min(8, len(lowered)) + 1):
                suffix = lowered[-length:]
                before = lowered[-length - 1] if len(lowered) > length else ""
                if (
                    any(prefix.startswith(suffix) for prefix in _INTERNAL_PREFIXES)
                    and (not before or not (before.isascii() and (before.isalnum() or before == "_")))
                ):
                    cut = len(self._buffer) - length
                    break
        if cut <= 0 and len(self._buffer) <= self._tail_chars:
            return ""
        if cut <= 0:
            cut = len(self._buffer) - self._tail_chars
        ready, self._buffer = self._buffer[:cut], self._buffer[cut:]
        public = sanitize_public_answer(ready, self._current_citations())
        if public:
            self._emitted_safe_text = True
        return public

    def flush(self) -> str:
        if self._blocked_internal_context:
            self._buffer = ""
            if self._emitted_safe_text:
                return ""
            self._emitted_safe_text = True
            return INTERNAL_CONTEXT_LEAK_ANSWER
        ready, self._buffer = self._buffer, ""
        public = sanitize_public_answer(ready, self._current_citations())
        if public:
            self._emitted_safe_text = True
        return public
