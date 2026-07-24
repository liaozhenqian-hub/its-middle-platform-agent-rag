from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field


Environment = Literal["develop", "test", "prod"]
EvidenceGrade = Literal["none", "log_only", "correlated", "contract_supported"]


class BugIntakeCandidate(BaseModel):
    normalized_problem: str = ""
    environment: Environment | None = None
    environment_evidence: str = ""
    trace_id: str | None = None
    service: str | None = None
    endpoint: str | None = None
    request_time: str | None = None
    request_time_evidence: str = ""
    symptoms: list[str] = Field(default_factory=list)
    domain_hints: list[str] = Field(default_factory=list)


class BugIntake(BugIntakeCandidate):
    original_message: str
    missing_fields: list[Literal["environment", "trace_id"]] = Field(
        default_factory=list
    )
    clarification_question: str = ""


class StackFrameState(TypedDict):
    symbol: str
    file: str
    line: int | None


class BugDiagnosisState(TypedDict, total=False):
    conversation_id: str
    run_id: str
    original_message: str
    latest_message: str
    normalized_problem: str
    environment: Environment | None
    environment_evidence: str
    trace_id: str | None
    service: str | None
    endpoint: str | None
    request_time: str | None
    request_time_evidence: str
    symptoms: list[str]
    domain_hints: list[str]
    missing_fields: list[str]
    clarification_question: str
    status: str
    current_stage: str
    created_at: str
    updated_at: str
    interrupted_at: str | None
    expires_at: str
    log_count: int
    exception_types: list[str]
    stack_frames: list[StackFrameState]
    logs_truncated: bool
    log_services: list[str]
    log_endpoints: list[str]
    code_chunk_ids: list[str]
    code_matches: list[dict[str, Any]]
    swagger_operations: list[dict[str, Any]]
    document_chunk_ids: list[str]
    entity_hints: list[str]
    selected_procedure_id: str | None
    selected_procedure_version: int | None
    procedure_capabilities: list[str]
    procedure_observe_only: bool
    citations: list[dict[str, Any]]
    evidence_grade: EvidenceGrade
    warnings: list[str]
    terminal_reason: str | None
    answer: str
