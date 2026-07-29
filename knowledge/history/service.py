from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator

import aiosqlite

from knowledge.persistence.database import DatabaseResources
from knowledge.persistence.sqlite_compat import PostgresCompatConnection

from knowledge.auth.repository import UserAuthRepository
from knowledge.history.models import (
    ConversationHistoryDetail,
    ConversationHistoryItem,
    ConversationHistoryPage,
    PublicConversationMessage,
)
from knowledge.agent_runtime.public_answer import sanitize_public_answer


class ConversationHistoryNotFound(LookupError):
    pass


class ConversationHistoryService:
    def __init__(
        self, auth_repository: UserAuthRepository, session_database_path: str | Path
    ):
        self.auth_repository = auth_repository
        self.session_database_path = Path(session_database_path)

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[aiosqlite.Connection]:
        database = await aiosqlite.connect(self.session_database_path)
        database.row_factory = aiosqlite.Row
        await database.execute("PRAGMA busy_timeout=5000")
        try:
            yield database
        finally:
            await database.close()

    async def list_conversations(
        self,
        owner_id: str,
        *,
        page: int = 1,
        page_size: int = 20,
        query: str = "",
    ) -> ConversationHistoryPage:
        if page < 1 or page_size < 1 or page_size > 100:
            raise ValueError("invalid conversation history pagination")
        normalized_query = " ".join(query.split()).casefold()
        owners = await self.auth_repository.list_conversations_for_owner(owner_id)
        items: list[ConversationHistoryItem] = []
        for owner in owners:
            detail = await self._read_detail(owner)
            if detail is None or not detail.messages:
                continue
            preview = _preview(detail.messages[-1].content, 140)
            item = ConversationHistoryItem(
                conversation_id=owner.conversation_id,
                title=detail.title,
                preview=preview,
                channel=owner.channel,
                message_count=len(detail.messages),
                created_at=owner.created_at,
                updated_at=owner.last_seen_at,
            )
            searchable = f"{item.title} {item.preview}".casefold()
            if not normalized_query or normalized_query in searchable:
                items.append(item)
        start = (page - 1) * page_size
        return ConversationHistoryPage(
            items=tuple(items[start:start + page_size]),
            total=len(items),
            page=page,
            page_size=page_size,
        )

    async def get_conversation(
        self, owner_id: str, conversation_id: str
    ) -> ConversationHistoryDetail:
        owner = await self._owned_conversation(owner_id, conversation_id)
        detail = await self._read_detail(owner)
        if detail is None:
            raise ConversationHistoryNotFound(conversation_id)
        return detail

    async def rename_conversation(
        self, owner_id: str, conversation_id: str, title: str
    ) -> ConversationHistoryItem:
        await self._owned_conversation(owner_id, conversation_id)
        try:
            owner = await self.auth_repository.rename_conversation(
                conversation_id, owner_id, title
            )
        except (KeyError, PermissionError) as exc:
            raise ConversationHistoryNotFound(conversation_id) from exc
        detail = await self._read_detail(owner)
        if detail is None:
            raise ConversationHistoryNotFound(conversation_id)
        return ConversationHistoryItem(
            conversation_id=conversation_id,
            title=detail.title,
            preview=_preview(detail.messages[-1].content, 140) if detail.messages else "",
            channel=owner.channel,
            message_count=len(detail.messages),
            created_at=owner.created_at,
            updated_at=owner.last_seen_at,
        )

    async def _owned_conversation(self, owner_id: str, conversation_id: str):
        try:
            owner = await self.auth_repository.get_conversation_owner(conversation_id)
        except KeyError as exc:
            raise ConversationHistoryNotFound(conversation_id) from exc
        if owner.owner_id != owner_id:
            raise ConversationHistoryNotFound(conversation_id)
        return owner

    async def _read_detail(self, owner) -> ConversationHistoryDetail | None:
        async with self._connect() as database:
            session = await (
                await database.execute(
                    "SELECT created_at,updated_at FROM agent_sessions WHERE session_id=?",
                    (owner.conversation_id,),
                )
            ).fetchone()
            if session is None:
                return None
            rows = await (
                await database.execute(
                    """
                    SELECT id,message_data,created_at FROM agent_messages
                    WHERE session_id=? ORDER BY id ASC
                    """,
                    (owner.conversation_id,),
                )
            ).fetchall()
            scope = await (
                await database.execute(
                    """
                    SELECT knowledge_space_id,domain_id
                    FROM agent_conversation_scopes WHERE conversation_id=?
                    """,
                    (owner.conversation_id,),
                )
            ).fetchone()
        messages = tuple(
            message
            for row in rows
            if (message := _public_message(row)) is not None
        )
        first_question = next(
            (item.content for item in messages if item.role == "user"), ""
        )
        return ConversationHistoryDetail(
            conversation_id=owner.conversation_id,
            title=owner.title or _preview(first_question, 60) or "新对话",
            channel=owner.channel,
            knowledge_space_id=scope["knowledge_space_id"] if scope else None,
            domain_id=scope["domain_id"] if scope else None,
            created_at=_parse_datetime(session["created_at"]),
            updated_at=_parse_datetime(session["updated_at"]),
            messages=messages,
        )


class PostgresConversationHistoryService(ConversationHistoryService):
    def __init__(self, auth_repository, database_resources: DatabaseResources):
        self.auth_repository = auth_repository
        self.database_resources = database_resources
        self.session_database_path = Path("postgres")

    @asynccontextmanager
    async def _connect(self):
        async with PostgresCompatConnection(self.database_resources) as connection:
            yield connection


def _public_message(row: aiosqlite.Row) -> PublicConversationMessage | None:
    try:
        payload: Any = json.loads(row["message_data"])
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("role") not in {"user", "assistant"}:
        return None
    role = payload["role"]
    content = _content_text(payload.get("content"))
    if not content:
        return None
    if role == "assistant":
        content = sanitize_public_answer(content)
    return PublicConversationMessage(
        id=int(row["id"]),
        role=role,
        content=content,
        created_at=_parse_datetime(row["created_at"]),
    )


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        if item.get("type") not in {"output_text", "input_text", "text"}:
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    return "\n".join(parts)


def _preview(value: str, limit: int) -> str:
    compact = " ".join(value.split())
    return compact if len(compact) <= limit else compact[: limit - 1].rstrip() + "…"


def _parse_datetime(value: str | datetime) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
