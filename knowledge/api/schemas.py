from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str | None = None
    message: str = Field(min_length=1)
    knowledge_space_id: str | None = None
    domain_id: str | None = None

    @field_validator("message")
    @classmethod
    def reject_blank_message(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("message cannot be blank")
        return normalized


class ConversationRenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=100)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("title cannot be blank")
        return normalized


class ConversationMessageResponse(BaseModel):
    id: int
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime


class ConversationHistoryItemResponse(BaseModel):
    conversation_id: str
    title: str
    preview: str
    channel: str
    message_count: int
    created_at: datetime
    updated_at: datetime


class ConversationHistoryPageResponse(BaseModel):
    items: list[ConversationHistoryItemResponse]
    total: int
    page: int
    page_size: int


class ConversationHistoryDetailResponse(BaseModel):
    conversation_id: str
    title: str
    channel: str
    knowledge_space_id: str | None
    domain_id: str | None
    created_at: datetime
    updated_at: datetime
    messages: list[ConversationMessageResponse]


class _CitationBase(BaseModel):
    source_id: str
    title: str = ""
    domain: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeChunkCitationResponse(_CitationBase):
    source_type: Literal["knowledge_chunk"]


class McpToolCitationResponse(_CitationBase):
    source_type: Literal["mcp_tool"]


class CodeCitationResponse(_CitationBase):
    source_type: Literal["code"]


class ProductDocumentCitationResponse(_CitationBase):
    source_type: Literal["product_document"]


class SwaggerCitationResponse(_CitationBase):
    source_type: Literal["swagger"]


class LogTraceCitationResponse(_CitationBase):
    source_type: Literal["log_trace"]


CitationResponse = Annotated[
    KnowledgeChunkCitationResponse
    | McpToolCitationResponse
    | CodeCitationResponse
    | ProductDocumentCitationResponse
    | SwaggerCitationResponse
    | LogTraceCitationResponse,
    Field(discriminator="source_type"),
]


class ToolRunResponse(BaseModel):
    tool_call_id: str
    tool_name: str
    agent_name: str
    status: str
    duration_ms: float | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)


class ApprovalResponse(BaseModel):
    tool_call_id: str
    tool_name: str
    status: str


class AgentResponse(BaseModel):
    status: Literal["completed", "approval_required"]
    conversation_id: str
    run_id: str
    answer: str | None
    last_agent: str
    citations: list[CitationResponse]
    tool_runs: list[ToolRunResponse]
    approvals: list[ApprovalResponse]
    routed_domains: list[str] = Field(default_factory=list)
    specialists_used: list[str] = Field(default_factory=list)
    trace_id: str | None = None
    quality_turn_id: str | None = None
    feedback_token: str | None = None


class CitationDetailResponse(BaseModel):
    source_type: Literal["code", "product_document", "knowledge_chunk", "swagger"]
    source_id: str
    title: str
    domain: str
    excerpt: str
    language: str | None = None
    truncated: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    content_scope: Literal["excerpt", "section", "full"] = "excerpt"
    full_text_available: bool = False
    document_url: str | None = None


class ToolDecision(BaseModel):
    tool_call_id: str = Field(min_length=1)
    decision: Literal["approve", "reject"]


class DecisionsRequest(BaseModel):
    decisions: list[ToolDecision] = Field(min_length=1)


class AdminLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=1000)


class DomainRuleRequest(BaseModel):
    pattern: str = Field(min_length=1, max_length=1000)
    domain_id: str = Field(min_length=1, max_length=200)
    priority: int = 100


class DomainRulesReplaceRequest(BaseModel):
    rules: list[DomainRuleRequest]


class GitSourceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    project_id: str = Field(min_length=1, max_length=300)
    project_path: str = Field(min_length=1, max_length=1000)
    project_url: str = Field(min_length=1, max_length=4000)
    project_web_url: str = Field(min_length=1, max_length=4000)
    branch: str = Field(min_length=1, max_length=1000)
    rules: list[DomainRuleRequest] = Field(default_factory=list)


class SwaggerSourceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    domain_id: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=4000)
    auth_type: Literal["none", "basic", "bearer"] = "none"
    username: str = Field(default="", max_length=1000)
    password: str = Field(default="", max_length=4000)
    bearer_token: str = Field(default="", max_length=8000)
    timeout_seconds: float = Field(default=15.0, gt=0, le=120)


class SourceDeleteRequest(BaseModel):
    confirm_name: str = Field(min_length=1, max_length=300)


class QualityFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feedback_token: str = Field(min_length=1, max_length=500)
    rating: Literal["positive", "negative"]
    reason: str = Field(default="", max_length=1000)
    reason_code: Literal[
        "", "inaccurate", "misunderstood", "irrelevant_citation",
        "reasked", "too_slow", "bad_format", "other"
    ] = ""


class QualityAnnotationReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_status: Literal["pending", "confirmed", "dismissed"]


class DomainMemoryPromotionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_memory_id: str = Field(min_length=1, max_length=200)
    target_domain_id: Literal["metric-platform", "approval-flow", "workflow"]
    public_summary: str = Field(min_length=1, max_length=1000)
    valid_until: datetime | None = None


class EvalCaseCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=300)
    required_tools: list[str] = Field(default_factory=list, max_length=50)
    required_citation_types: list[str] = Field(default_factory=list, max_length=20)
    required_facts: list[str] = Field(default_factory=list, max_length=100)
    forbidden_facts: list[str] = Field(default_factory=list, max_length=100)
    tags: list[str] = Field(default_factory=list, max_length=50)
    enabled: bool = True
    expected_behavior: Literal["answer", "clarify", "refuse"] = "answer"
    max_latency_ms: float = Field(default=60_000, gt=0)
    max_tool_calls: int = Field(default=6, ge=0)
    max_citations: int = Field(default=10, ge=0)
    turns: list[str] = Field(default_factory=list, max_length=20)
    task_type: Literal[
        "unknown", "how_to", "api_contract", "code_lookup",
        "requirement_analysis", "metric_query", "bug"
    ] = "unknown"
    suite: str = Field(default="routing-breadth", max_length=100)
    priority: Literal["normal", "high", "critical"] = "normal"
    approval_state: Literal["candidate", "approved", "rejected"] = "candidate"


class EvalCaseUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=300)
    question: str = Field(min_length=1, max_length=20000)
    knowledge_space_id: str = Field(min_length=1, max_length=200)
    domain_id: str | None = Field(default=None, max_length=200)
    required_tools: list[str] = Field(default_factory=list, max_length=50)
    required_citation_types: list[str] = Field(default_factory=list, max_length=20)
    required_facts: list[str] = Field(default_factory=list, max_length=100)
    forbidden_facts: list[str] = Field(default_factory=list, max_length=100)
    tags: list[str] = Field(default_factory=list, max_length=50)
    enabled: bool = True
    expected_behavior: Literal["answer", "clarify", "refuse"] = "answer"
    max_latency_ms: float = Field(default=60_000, gt=0)
    max_tool_calls: int = Field(default=6, ge=0)
    max_citations: int = Field(default=10, ge=0)
    turns: list[str] = Field(default_factory=list, max_length=20)
    task_type: Literal[
        "unknown", "how_to", "api_contract", "code_lookup",
        "requirement_analysis", "metric_query", "bug"
    ] = "unknown"
    suite: str = Field(default="routing-breadth", max_length=100)
    priority: Literal["normal", "high", "critical"] = "normal"
    approval_state: Literal["candidate", "approved", "rejected"] = "candidate"


class EvalRunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_ids: list[str] | None = Field(default=None, min_length=1, max_length=500)
