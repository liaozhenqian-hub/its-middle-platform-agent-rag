from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any


_PRIVATE_KEYS = {
    "api_key",
    "authorization",
    "bearer_token",
    "chunk_content",
    "content",
    "embedding",
    "output",
    "password",
    "prompt",
    "secret",
    "token",
}


def _is_private_key(key: Any) -> bool:
    normalized = str(key).strip().lower().replace("-", "_")
    compact = normalized.replace("_", "")
    return normalized in _PRIVATE_KEYS or any(
        fragment in compact
        for fragment in (
            "apikey",
            "authorization",
            "content",
            "embedding",
            "modeloutput",
            "password",
            "prompt",
            "secret",
            "token",
        )
    )


def redact_mapping(value: Any) -> Any:
    """Remove credentials and large model/knowledge payloads from public audit data."""
    if isinstance(value, dict):
        return {
            str(key): redact_mapping(item)
            for key, item in value.items()
            if not _is_private_key(key)
        }
    if isinstance(value, (list, tuple)):
        return [redact_mapping(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


@dataclass
class Citation:
    source_type: str
    source_id: str
    title: str = ""
    domain: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolRun:
    tool_call_id: str
    tool_name: str
    agent_name: str
    status: str = "started"
    duration_ms: float | None = None
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class ApprovalRecord:
    tool_call_id: str
    tool_name: str
    status: str = "pending"


@dataclass
class RuntimeSpan:
    kind: str
    name: str
    status: str
    duration_ms: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass
class AgentRunContext:
    conversation_id: str
    run_id: str
    knowledge_space_id: str = "middle-platform"
    domain_id: str | None = None
    user_id: str | None = None
    trace_id: str | None = None
    response_mode: str = "answer"
    response_override: str | None = None
    routing_domains: list[str] = field(default_factory=list)
    routing_intent: str = "unknown"
    task_type: str = "unknown"
    current_user_message: str = field(default="", repr=False, compare=False)
    metric_confirmation_token: str | None = None
    metric_confirmed_app: str | None = None
    citations: list[Citation] = field(default_factory=list)
    tool_runs: list[ToolRun] = field(default_factory=list)
    approvals: list[ApprovalRecord] = field(default_factory=list)
    runtime_spans: list[RuntimeSpan] = field(default_factory=list, repr=False, compare=False)
    retrieval_signatures: dict[str, int] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )
    retrieval_call_count: int = field(default=0, repr=False, compare=False)

    def reserve_retrieval(
        self,
        tool_name: str,
        query: str,
        max_calls: int,
        max_identical_queries: int,
    ) -> str:
        """Reserve one retrieval call without persisting run-local query state."""
        normalized_query = re.sub(r"[\W_]+", "", query.casefold(), flags=re.UNICODE)
        signature = f"{tool_name}:{normalized_query}"
        if self.retrieval_signatures.get(signature, 0) >= max_identical_queries:
            return "duplicate"
        if self.retrieval_call_count >= max_calls:
            return "budget_exhausted"
        self.retrieval_signatures[signature] = self.retrieval_signatures.get(signature, 0) + 1
        self.retrieval_call_count += 1
        return "allowed"

    def public_citations(self, max_count: int) -> list[Citation]:
        """Return stable, logically deduplicated citations for public responses."""
        if max_count <= 0:
            return []

        unique: list[Citation] = []
        seen: set[tuple[Any, ...]] = set()
        public_titles: set[tuple[str, str]] = set()
        for citation in self.citations:
            metadata = citation.metadata
            if citation.source_type == "code":
                key = (
                    "code",
                    metadata.get("branch"),
                    metadata.get("relative_path") or metadata.get("path"),
                    metadata.get("symbol_name") or metadata.get("symbol"),
                )
            elif citation.source_type == "product_document":
                key = (
                    "product_document",
                    metadata.get("source_id"),
                    metadata.get("relative_path") or metadata.get("path"),
                    metadata.get("heading") or citation.title,
                )
            else:
                key = (citation.source_type, citation.source_id)
            normalized_title = " ".join(citation.title.casefold().split())
            title_key = (citation.source_type, normalized_title)
            if (
                normalized_title
                and citation.source_type in {"code", "product_document"}
                and title_key in public_titles
            ):
                continue
            if key not in seen:
                seen.add(key)
                if normalized_title and citation.source_type in {
                    "code",
                    "product_document",
                }:
                    public_titles.add(title_key)
                unique.append(citation)

        if len(unique) <= max_count:
            return unique

        selected_indexes: set[int] = set()
        seen_types: set[str] = set()
        for index, citation in enumerate(unique):
            if citation.source_type not in seen_types:
                selected_indexes.add(index)
                seen_types.add(citation.source_type)
                if len(selected_indexes) == max_count:
                    break
        for index in range(len(unique)):
            if len(selected_indexes) == max_count:
                break
            selected_indexes.add(index)
        return [citation for index, citation in enumerate(unique) if index in selected_indexes]

    def add_knowledge_citation(
        self,
        chunk_id: str,
        heading: str,
        domain: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        public_metadata = redact_mapping(metadata or {})
        source_type = str(public_metadata.get("source_type") or "knowledge_chunk")
        if source_type not in {"code", "product_document"}:
            source_type = "knowledge_chunk"
        citation = Citation(
            source_type=source_type,
            source_id=chunk_id,
            title=heading,
            domain=domain,
            metadata=public_metadata,
        )
        if citation not in self.citations:
            self.citations.append(citation)

    def add_mcp_citation(
        self,
        tool_name: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        citation = Citation(
            source_type="mcp_tool",
            source_id=tool_name,
            title=tool_name,
            domain="指标平台",
            metadata=redact_mapping(metadata or {}),
        )
        if citation not in self.citations:
            self.citations.append(citation)

    def add_swagger_citation(
        self,
        source_id: str,
        domain: str,
        operation: dict[str, Any],
        refreshed_at: str,
        stale: bool,
    ) -> None:
        public_operation = redact_mapping(operation)
        operation_id = str(public_operation.get("operation_id") or "").strip()
        method = str(public_operation.get("method") or "UNKNOWN").strip().upper()
        path = str(public_operation.get("path") or "/unknown").strip()
        operation_identity = operation_id or f"{method}:{path}"
        title = operation_id or f"{method} {path}"
        citation = Citation(
            source_type="swagger",
            source_id=f"{source_id}:{operation_identity}",
            title=title,
            domain=domain,
            metadata={
                **public_operation,
                "swagger_source_id": source_id,
                "refreshed_at": refreshed_at,
                "stale": stale,
            },
        )
        if citation not in self.citations:
            self.citations.append(citation)

    def add_log_trace_citation(
        self,
        *,
        trace_id: str,
        environment: str,
        from_ms: int,
        to_ms: int,
        log_count: int,
        exception_types: list[str] | tuple[str, ...],
        truncated: bool,
        entries: Any | None = None,
    ) -> None:
        # Raw log entries are intentionally accepted only so callers cannot
        # accidentally merge them into the public citation metadata.
        del entries
        citation = Citation(
            source_type="log_trace",
            source_id=trace_id,
            title=f"{environment} trace {trace_id}",
            domain="中台",
            metadata={
                "environment": environment,
                "from_ms": from_ms,
                "to_ms": to_ms,
                "log_count": log_count,
                "exception_types": list(exception_types),
                "truncated": truncated,
            },
        )
        if citation not in self.citations:
            self.citations.append(citation)

    def start_tool(
        self,
        tool_call_id: str,
        tool_name: str,
        agent_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> None:
        public_arguments = redact_mapping(arguments or {})
        for tool_run in reversed(self.tool_runs):
            if tool_run.tool_call_id == tool_call_id:
                tool_run.tool_name = tool_name
                tool_run.agent_name = agent_name
                tool_run.arguments = public_arguments
                return
        self.tool_runs.append(
            ToolRun(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                agent_name=agent_name,
                arguments=public_arguments,
            )
        )

    def finish_tool(
        self,
        tool_call_id: str,
        status: str,
        duration_ms: float | None = None,
    ) -> None:
        for tool_run in reversed(self.tool_runs):
            if tool_run.tool_call_id == tool_call_id:
                tool_run.status = status
                tool_run.duration_ms = duration_ms
                return

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("current_user_message", None)
        data.pop("retrieval_signatures", None)
        data.pop("retrieval_call_count", None)
        data.pop("runtime_spans", None)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentRunContext:
        return cls(
            conversation_id=str(data["conversation_id"]),
            run_id=str(data["run_id"]),
            knowledge_space_id=str(data.get("knowledge_space_id") or "middle-platform"),
            domain_id=data.get("domain_id"),
            user_id=data.get("user_id"),
            trace_id=data.get("trace_id"),
            response_mode=str(data.get("response_mode") or "answer"),
            response_override=data.get("response_override"),
            routing_domains=[str(item) for item in data.get("routing_domains", [])],
            routing_intent=str(data.get("routing_intent") or "unknown"),
            task_type=str(data.get("task_type") or "unknown"),
            metric_confirmation_token=data.get("metric_confirmation_token"),
            metric_confirmed_app=data.get("metric_confirmed_app"),
            citations=[Citation(**item) for item in data.get("citations", [])],
            tool_runs=[ToolRun(**item) for item in data.get("tool_runs", [])],
            approvals=[ApprovalRecord(**item) for item in data.get("approvals", [])],
        )
