from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass(frozen=True, slots=True)
class PublicConversationMessage:
    id: int
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ConversationHistoryItem:
    conversation_id: str
    title: str
    preview: str
    channel: str
    message_count: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ConversationHistoryPage:
    items: tuple[ConversationHistoryItem, ...]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True, slots=True)
class ConversationHistoryDetail:
    conversation_id: str
    title: str
    channel: str
    knowledge_space_id: str | None
    domain_id: str | None
    created_at: datetime
    updated_at: datetime
    messages: tuple[PublicConversationMessage, ...]
