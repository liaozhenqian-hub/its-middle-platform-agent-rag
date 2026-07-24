from __future__ import annotations

from datetime import datetime

from knowledge.auth.models import ConversationOwner
from knowledge.auth.repository import UserAuthRepository


class ConversationNotFoundError(LookupError):
    pass


class ConversationOwnershipService:
    def __init__(self, repository: UserAuthRepository):
        self.repository = repository

    async def claim(
        self,
        conversation_id: str,
        owner_id: str,
        *,
        channel: str,
        now: datetime | None = None,
    ) -> ConversationOwner:
        try:
            return await self.repository.bind_conversation_owner(
                conversation_id, owner_id, channel=channel, now=now
            )
        except PermissionError as exc:
            raise ConversationNotFoundError("conversation not found") from exc

    async def release(self, conversation_id: str, owner_id: str) -> bool:
        try:
            return await self.repository.delete_conversation_owner(
                conversation_id, owner_id
            )
        except PermissionError as exc:
            raise ConversationNotFoundError("conversation not found") from exc
