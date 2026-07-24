from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class SourceType(str, Enum):
    GIT = "git"
    DOCUMENT = "document"
    SWAGGER = "swagger"


class SyncJobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class KnowledgeSpace:
    id: str
    name: str
    created_at: datetime


@dataclass(frozen=True)
class KnowledgeDomain:
    id: str
    space_id: str
    name: str
    sort_order: int
    created_at: datetime


@dataclass(frozen=True)
class KnowledgeSourceCreate:
    id: str
    space_id: str
    domain_id: str | None
    source_type: SourceType
    name: str
    config: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True


@dataclass(frozen=True)
class KnowledgeSource:
    id: str
    space_id: str
    domain_id: str | None
    source_type: SourceType
    name: str
    config: dict[str, Any]
    enabled: bool
    credential_configured: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class SourceDomainRuleCreate:
    id: str
    source_id: str
    pattern: str
    target_domain_id: str | None = None
    shared: bool = False
    priority: int = 100

    def __post_init__(self) -> None:
        if self.shared == (self.target_domain_id is not None):
            raise ValueError(
                "a domain rule must target exactly one domain or the shared scope"
            )


@dataclass(frozen=True)
class SourceDomainRule:
    id: str
    source_id: str
    pattern: str
    target_domain_id: str | None
    shared: bool
    priority: int
    created_at: datetime


@dataclass(frozen=True)
class SourceVersionCreate:
    id: str
    source_id: str
    version_ref: str
    status: str
    current: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceVersion:
    id: str
    source_id: str
    version_ref: str
    status: str
    current: bool
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class SourceFileCreate:
    id: str
    source_id: str
    version_id: str
    relative_path: str
    domain_key: str
    language: str | None
    content_hash: str
    size_bytes: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceFile:
    id: str
    source_id: str
    version_id: str
    relative_path: str
    domain_key: str
    language: str | None
    content_hash: str
    size_bytes: int
    metadata: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True)
class CodeSymbolCreate:
    id: str
    source_file_id: str
    symbol_type: str
    name: str
    qualified_name: str | None
    start_line: int
    end_line: int
    parent_symbol_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CodeSymbol:
    id: str
    source_file_id: str
    symbol_type: str
    name: str
    qualified_name: str | None
    start_line: int
    end_line: int
    parent_symbol_id: str | None
    metadata: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True)
class ChunkCatalogCreate:
    chunk_id: str
    source_id: str
    version_id: str
    source_file_id: str | None
    source_type: SourceType
    domain_key: str
    locator: str
    content_hash: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChunkCatalogEntry:
    chunk_id: str
    source_id: str
    version_id: str
    source_file_id: str | None
    source_type: SourceType
    domain_key: str
    locator: str
    content_hash: str
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class SyncJob:
    id: str
    source_id: str
    kind: str
    state: SyncJobState
    target_commit: str | None
    attempt: int
    error: str | None
    worker_id: str | None
    available_at: datetime
    claimed_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class AuditEvent:
    id: str
    actor: str
    action: str
    resource_type: str
    resource_id: str | None
    details: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True)
class AdminSessionCredentials:
    token: str
    csrf_token: str
    expires_at: datetime


@dataclass(frozen=True)
class AdminSession:
    id: str
    username: str
    expires_at: datetime
    created_at: datetime

