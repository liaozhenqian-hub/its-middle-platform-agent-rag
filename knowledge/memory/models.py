from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


MemoryScope = Literal["user", "conversation", "team", "domain", "global"]
MemoryType = Literal[
    "user_preference",
    "user_context",
    "episodic_memory",
    "decision_memory",
    "procedural_memory",
]
MemoryStatus = Literal["candidate", "confirmed", "rejected", "expired", "deleted"]


@dataclass(frozen=True)
class MemoryCandidateCreate:
    scope_type: MemoryScope
    owner_id: str
    space_id: str
    domain_id: str | None
    memory_type: MemoryType
    subject: str
    normalized_fact: str
    summary: str
    source_turn_id: str | None
    source_citations: tuple[str, ...] = ()
    confidence: float = 0.0
    expires_at: datetime | None = None
    id: str | None = None


@dataclass(frozen=True)
class MemoryCandidate:
    id: str
    scope_type: MemoryScope
    owner_id: str
    space_id: str
    domain_id: str | None
    memory_type: MemoryType
    subject: str
    normalized_fact: str
    summary: str
    source_turn_id: str | None
    source_citations: tuple[str, ...]
    confidence: float
    status: MemoryStatus
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class Memory:
    id: str
    scope_type: MemoryScope
    owner_id: str
    space_id: str
    domain_id: str | None
    memory_type: MemoryType
    subject: str
    normalized_fact: str
    summary: str
    source_turn_id: str | None
    source_citations: tuple[str, ...]
    confidence: float
    status: MemoryStatus
    valid_from: datetime
    valid_until: datetime | None
    last_used_at: datetime | None
    supersedes_id: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class MemoryExtractionJob:
    id: str
    user_id: str
    conversation_id: str
    space_id: str
    domain_id: str | None
    channel: str
    question: str
    answer: str | None
    source_turn_id: str | None
    source_citations: tuple[str, ...]
    status: str
    attempt: int
    worker_id: str | None
    error_type: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ConversationSummary:
    conversation_id: str
    user_id: str
    space_id: str
    domain_id: str | None
    summary: str
    goals: tuple[str, ...]
    confirmed_facts: tuple[str, ...]
    unresolved_items: tuple[str, ...]
    preferences: tuple[str, ...]
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class ProceduralStep:
    capability: str
    purpose: str
    required_inputs: tuple[str, ...] = ()
    produced_signals: tuple[str, ...] = ()
    next_condition: str | None = None


@dataclass(frozen=True)
class ProceduralSpec:
    task_type: str
    procedure_version: int
    trigger_conditions: tuple[str, ...]
    required_inputs: tuple[str, ...]
    environment_constraints: tuple[str, ...]
    branch_constraints: tuple[str, ...]
    steps: tuple[ProceduralStep, ...]
    allowed_tools: tuple[str, ...]
    minimum_evidence_grade: str
    stop_conditions: tuple[str, ...]
    fallback_actions: tuple[str, ...]
    expected_output: tuple[str, ...]
    validation_steps: tuple[str, ...]
    success_count: int = 0
    failure_count: int = 0
    last_executed_at: datetime | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None


@dataclass(frozen=True)
class DomainPromotion:
    id: str
    source_memory_id: str
    target_candidate_id: str | None
    target_domain_id: str
    public_summary: str
    state: str
    requested_by: str
    reviewed_by: str | None
    reviewed_at: datetime | None
    valid_until: datetime | None
    created_at: datetime
    updated_at: datetime
