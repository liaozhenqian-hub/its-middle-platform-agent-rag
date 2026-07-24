from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

from knowledge.feishu.models import FeishuIncomingMessage
from knowledge.agent_runtime.public_answer import public_citation_name, sanitize_public_answer


_CITATION_LABELS = {
    "knowledge_chunk": "知识片段",
    "mcp_tool": "MCP 工具",
    "code": "代码",
    "product_document": "产品文档",
    "swagger": "Swagger",
    "log_trace": "日志 Trace",
}


def parse_message_event(
    payload: dict[str, Any],
    *,
    require_group_mention: bool,
    bot_open_id: str = "",
) -> FeishuIncomingMessage | None:
    if str(payload.get("sender_type") or "") != "user":
        return None
    if str(payload.get("message_type") or "") != "text":
        return None
    event_id = str(payload.get("event_id") or "").strip()
    message_id = str(payload.get("message_id") or "").strip()
    chat_id = str(payload.get("chat_id") or "").strip()
    chat_type = str(payload.get("chat_type") or "").strip()
    if not event_id or not message_id or not chat_id:
        return None
    try:
        content = json.loads(str(payload.get("content") or ""))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(content, dict):
        return None
    text = str(content.get("text") or "")
    mentions = payload.get("mentions") or []
    if chat_type == "group" and require_group_mention:
        if not bot_open_id or not isinstance(mentions, list):
            return None
        mentioned_bot = any(
            isinstance(mention, dict)
            and str(mention.get("open_id") or "") == bot_open_id
            for mention in mentions
        )
        if not mentioned_bot:
            return None
    if isinstance(mentions, list):
        for mention in mentions:
            if isinstance(mention, dict):
                key = str(mention.get("key") or "")
                if key:
                    text = text.replace(key, " ")
    normalized = " ".join(text.split()).strip(" ,，:：")
    if not normalized:
        return None
    return FeishuIncomingMessage(
        event_id=event_id,
        message_id=message_id,
        chat_id=chat_id,
        chat_type=chat_type,
        text=normalized,
        sender_id=str(payload.get("sender_id") or "").strip(),
        sender_name=str(payload.get("sender_name") or "").strip(),
        thread_id=str(payload.get("thread_id") or "").strip(),
        root_id=str(payload.get("root_id") or "").strip(),
        parent_id=str(payload.get("parent_id") or "").strip(),
    )


def format_agent_reply(response: Any) -> str:
    citations = list(getattr(response, "citations", None) or [])
    answer = adapt_markdown_for_feishu(
        sanitize_public_answer(
            str(getattr(response, "answer", None) or "本次请求未生成回答。").strip(),
            citations,
        )
    )
    if not citations:
        return answer
    lines = [answer, "", "---", "**引用依据**"]
    for index, citation in enumerate(citations, 1):
        source_type = str(getattr(citation, "source_type", "") or "")
        metadata = getattr(citation, "metadata", None)
        metadata = metadata if isinstance(metadata, dict) else {}
        label = _CITATION_LABELS.get(source_type, "引用")
        link = _safe_http_url(str(metadata.get("gitlab_url") or ""))
        display_title = _escape_markdown_text(public_citation_name(citation))
        if link:
            display_title = f"[{display_title}]({link})"
        lines.append(f"{index}. **{label}** · {display_title}")
        details = _citation_details(source_type, metadata)
        if details:
            lines.append(f"   {' · '.join(details)}")
    return "\n".join(lines)


def adapt_markdown_for_feishu(markdown: str) -> str:
    """Convert standard Markdown into the subset rendered reliably by Feishu cards."""
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    output: list[str] = []
    index = 0
    in_code_fence = False
    while index < len(lines):
        line = lines[index]
        if line.lstrip().startswith("```"):
            in_code_fence = not in_code_fence
            output.append(line)
            index += 1
            continue
        if not in_code_fence:
            heading = re.fullmatch(r"\s{0,3}#{1,6}\s+(.+?)\s*#*\s*", line)
            if heading:
                title = _without_inline_code(heading.group(1)).strip()
                if title.startswith("**") and title.endswith("**"):
                    title = title[2:-2].strip()
                output.append(f"**{title}**")
                index += 1
                continue
        if (
            not in_code_fence
            and index + 1 < len(lines)
            and _is_table_header(line, lines[index + 1])
        ):
            headers = _table_cells(line)
            index += 2
            rows: list[list[str]] = []
            while index < len(lines) and "|" in lines[index]:
                cells = _table_cells(lines[index])
                if cells:
                    rows.append(cells)
                index += 1
            if len(headers) == 2 and headers[1].strip() in {"内容", "值"}:
                for row in rows:
                    values = row + [""] * max(0, 2 - len(row))
                    label = _without_inline_code(values[0]).strip()
                    value = _without_inline_code(values[1]).strip()
                    if label and value:
                        output.append(f"**{label}**：{value}")
                output.append("")
                continue
            for row_number, row in enumerate(rows, 1):
                values = row + [""] * max(0, len(headers) - len(row))
                title = _without_inline_code(values[0]) or f"第 {row_number} 项"
                output.append(f"**{title}**")
                for header, value in zip(headers[1:], values[1:]):
                    if header and value:
                        output.append(
                            f"- **{_without_inline_code(header)}**："
                            f"{_without_inline_code(value)}"
                        )
                output.append("")
            continue
        output.append(line if in_code_fence else _without_inline_code(line))
        index += 1
    return "\n".join(output).strip()


def _without_inline_code(value: str) -> str:
    return re.sub(r"`([^`\n]+)`", r"\1", value)


def _is_table_header(header: str, separator: str) -> bool:
    headers = _table_cells(header)
    separators = _table_cells(separator)
    return bool(
        len(headers) >= 2
        and len(headers) == len(separators)
        and all(re.fullmatch(r":?-{3,}:?", cell) for cell in separators)
    )


def _table_cells(line: str) -> list[str]:
    normalized = line.strip()
    if normalized.startswith("|"):
        normalized = normalized[1:]
    if normalized.endswith("|"):
        normalized = normalized[:-1]
    return [cell.strip() for cell in normalized.split("|")]


def _citation_details(
    source_type: str,
    metadata: dict[str, Any],
) -> list[str]:
    details: list[str] = []
    if source_type == "code":
        for key in ("branch", "relative_path"):
            value = str(metadata.get(key) or "").strip()
            if value:
                details.append(_inline_code(value))
    elif source_type == "log_trace":
        environment = str(metadata.get("environment") or "").strip()
        log_count = metadata.get("log_count")
        if environment:
            details.append(f"环境: {_inline_code(environment)}")
        if isinstance(log_count, int):
            details.append(f"日志: {log_count} 条")
    elif source_type == "swagger":
        method = str(metadata.get("method") or "").strip().upper()
        path = str(metadata.get("path") or "").strip()
        if method:
            details.append(_inline_code(method))
        if path:
            details.append(_inline_code(path))
    elif source_type == "product_document":
        for key in ("relative_path", "source_version"):
            value = str(metadata.get(key) or "").strip()
            if value:
                details.append(_inline_code(value))
    return details


def _safe_http_url(value: str) -> str:
    parsed = urlparse(value.strip())
    return value.strip() if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def _escape_markdown_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _inline_code(value: str) -> str:
    return value.replace("`", "")


def split_reply(text: str, *, max_chars: int) -> list[str]:
    normalized = text.strip()
    if not normalized:
        return []
    paragraphs = normalized.split("\n\n")
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if not paragraph:
            continue
        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        while len(paragraph) > max_chars:
            chunks.append(paragraph[:max_chars])
            paragraph = paragraph[max_chars:]
        current = paragraph
    if current:
        chunks.append(current)
    return chunks
