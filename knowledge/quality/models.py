from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TurnStart:
    run_id: str
    question: str
    channel: str
    conversation_id: str = ""
    channel_message_id: str | None = None
    user_id: str | None = None
    user_name: str | None = None
    chat_id: str | None = None
    knowledge_space_id: str = "middle-platform"
    domain_id: str | None = None
    provider: str = ""
    model_name: str = ""
    application_version: str = ""
    prompt_version: str = ""


@dataclass(frozen=True)
class ToolRunSnapshot:
    tool_call_id: str
    tool_name: str
    agent_name: str
    status: str
    duration_ms: float | None = None
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CitationSnapshot:
    source_type: str
    source_id: str
    title: str = ""
    domain: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TurnCompletion:
    status: str
    answer: str | None = None
    last_agent: str = ""
    domain_id: str | None = None
    duration_ms: float | None = None
    error_type: str | None = None
    routed_domains: list[str] = field(default_factory=list)
    specialists_used: list[str] = field(default_factory=list)
    response_mode: str = "answer"
    spans: list[QualitySpanSnapshot] = field(default_factory=list)
    tools: list[ToolRunSnapshot] = field(default_factory=list)
    citations: list[CitationSnapshot] = field(default_factory=list)


@dataclass(frozen=True)
class FeedbackRecord:
    id: str
    turn_id: str
    channel: str
    user_id: str | None
    user_name: str | None
    rating: str
    reason: str
    created_at: str
    updated_at: str
    reason_code: str = ""


@dataclass(frozen=True)
class QualityTurn:
    id: str
    run_id: str
    conversation_id: str
    channel: str
    channel_message_id: str | None
    channel_reply_message_id: str | None
    user_id: str | None
    user_name: str | None
    chat_id: str | None
    question: str
    answer: str | None
    knowledge_space_id: str
    domain_id: str | None
    status: str
    provider: str
    model_name: str
    last_agent: str
    application_version: str
    prompt_version: str
    duration_ms: float | None
    error_type: str | None
    created_at: str
    updated_at: str
    completed_at: str | None
    feedback_token: str = ""
    tools: list[ToolRunSnapshot] = field(default_factory=list)
    citations: list[CitationSnapshot] = field(default_factory=list)
    feedback: list[FeedbackRecord] = field(default_factory=list)
    routed_domains: list[str] = field(default_factory=list)
    specialists_used: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class QualitySpanCreate:
    turn_id: str
    run_id: str
    kind: str
    name: str
    status: str
    duration_ms: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QualitySpanSnapshot:
    kind: str
    name: str
    status: str
    duration_ms: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QualitySpan:
    id: str
    turn_id: str
    run_id: str
    kind: str
    name: str
    status: str
    duration_ms: float | None
    input_tokens: int
    output_tokens: int
    total_tokens: int
    metadata: dict[str, Any]
    created_at: str


@dataclass(frozen=True)
class QualityAnnotationCreate:
    turn_id: str
    source: str
    code: str
    severity: str = "warning"
    confidence: float = 1.0
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QualityAnnotation:
    id: str
    turn_id: str
    source: str
    code: str
    severity: str
    confidence: float
    details: dict[str, Any]
    review_status: str
    reviewer: str | None
    reviewed_at: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class QualityAnnotationPage:
    items: list[QualityAnnotation]
    page: int
    page_size: int
    total: int


@dataclass(frozen=True)
class QualityAnalytics:
    total_turns: int
    completed_turns: int
    citation_coverage: float
    average_tool_calls: float
    feedback_rate: float
    p50_duration_ms: float | None
    p90_duration_ms: float | None
    issue_counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class QualityTurnPage:
    items: list[QualityTurn]
    page: int
    page_size: int
    total: int


@dataclass(frozen=True)
class EvalCaseCreate:
    name: str
    question: str
    source_turn_id: str | None = None
    knowledge_space_id: str = "middle-platform"
    domain_id: str | None = None
    required_tools: list[str] = field(default_factory=list)
    required_citation_types: list[str] = field(default_factory=list)
    required_facts: list[str] = field(default_factory=list)
    forbidden_facts: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    enabled: bool = True
    expected_behavior: str = "answer"
    max_latency_ms: float = 60_000
    max_tool_calls: int = 6
    max_citations: int = 10
    turns: list[str] = field(default_factory=list)
    task_type: str = "unknown"
    suite: str = "routing-breadth"
    priority: str = "normal"
    approval_state: str = "candidate"

    def __post_init__(self) -> None:
        if self.expected_behavior not in {"answer", "clarify", "refuse"}:
            raise ValueError("expected_behavior must be answer, clarify, or refuse")
        if self.max_latency_ms <= 0:
            raise ValueError("max_latency_ms must be positive")
        if self.max_tool_calls < 0 or self.max_citations < 0:
            raise ValueError("evaluation output budgets cannot be negative")


@dataclass(frozen=True)
class EvalCase:
    id: str
    source_turn_id: str | None
    name: str
    question: str
    knowledge_space_id: str
    domain_id: str | None
    required_tools: list[str]
    required_citation_types: list[str]
    required_facts: list[str]
    forbidden_facts: list[str]
    tags: list[str]
    enabled: bool
    expected_behavior: str
    max_latency_ms: float
    max_tool_calls: int
    max_citations: int
    created_at: str
    updated_at: str
    turns: list[str] = field(default_factory=list)
    task_type: str = "unknown"
    suite: str = "routing-breadth"
    priority: str = "normal"
    approval_state: str = "candidate"
    version: int = 1


@dataclass(frozen=True)
class EvalRun:
    id: str
    status: str
    application_version: str
    provider: str
    model_name: str
    total_cases: int
    passed_cases: int
    failed_cases: int
    created_at: str
    completed_at: str | None
    case_ids: list[str] = field(default_factory=list)
    config_snapshot: dict[str, Any] = field(default_factory=dict)
    cancel_requested: bool = False
    current_case: int = 0


@dataclass(frozen=True)
class EvalResult:
    id: str
    run_id: str
    case_id: str
    status: str
    answer: str | None
    last_agent: str
    tool_names: list[str]
    citation_types: list[str]
    duration_ms: float | None
    checks: dict[str, bool]
    passed: bool
    error_type: str | None
    created_at: str
    judge_score: float | None = None
    judge: dict[str, Any] = field(default_factory=dict)
    failure_codes: list[str] = field(default_factory=list)
    review_state: str = "not_required"
    case_snapshot: dict[str, Any] = field(default_factory=dict)
