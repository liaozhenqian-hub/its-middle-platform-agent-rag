from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


IdentityKind = Literal["anonymous", "feishu", "personal_token"]


@dataclass(frozen=True, slots=True)
class ResolvedIdentity:
    owner_id: str
    kind: IdentityKind
    display_name: str
    scopes: frozenset[str] = field(default_factory=frozenset)
    session_id: str | None = None
    csrf_token: str | None = None
    token_id: str | None = None


@dataclass(frozen=True, slots=True)
class AnonymousDevice:
    id: str
    owner_id: str
    token_hash: str
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    disabled_at: datetime | None = None
    merged_to_open_id: str | None = None


@dataclass(frozen=True, slots=True)
class FeishuUser:
    open_id: str
    tenant_key: str
    display_name: str
    avatar_url: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class OAuthLoginState:
    id: str
    state_hash: str
    anonymous_owner_id: str | None
    redirect_path: str
    expires_at: datetime
    created_at: datetime
    consumed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class UserSession:
    id: str
    token_hash: str
    open_id: str
    csrf_token: str
    source_anonymous_owner_id: str | None
    created_at: datetime
    last_seen_at: datetime
    sliding_expires_at: datetime
    absolute_expires_at: datetime
    revoked_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PersonalApiToken:
    id: str
    open_id: str
    name: str
    token_hash: str
    display_prefix: str
    scopes: frozenset[str]
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ConversationOwner:
    conversation_id: str
    owner_id: str
    channel: str
    title: str | None
    created_at: datetime
    last_seen_at: datetime


@dataclass(frozen=True, slots=True)
class IdentityMergeJob:
    id: str
    source_anonymous_owner_id: str
    target_open_id: str
    status: Literal["pending", "running", "completed", "failed"]
    result: dict[str, int]
    error_type: str | None
    created_at: datetime
    updated_at: datetime
