from __future__ import annotations

from datetime import datetime
from typing import Protocol

from knowledge.auth.models import IdentityMergeJob
from knowledge.auth.repository import UserAuthRepository


class MergeMemoryRepository(Protocol):
    async def preview_user_owner_merge(
        self, source_owner_id: str, target_owner_id: str
    ) -> dict[str, int]: ...

    async def merge_user_owner(
        self, source_owner_id: str, target_owner_id: str
    ) -> dict[str, int]: ...


class IdentityMergeService:
    def __init__(
        self,
        auth_repository: UserAuthRepository,
        memory_repository: MergeMemoryRepository,
    ):
        self.auth_repository = auth_repository
        self.memory_repository = memory_repository

    async def preview(
        self, source_anonymous_owner_id: str, target_open_id: str
    ) -> dict[str, int]:
        memory = await self.memory_repository.preview_user_owner_merge(
            source_anonymous_owner_id, target_open_id
        )
        return {
            **memory,
            "conversations": await self.auth_repository.count_conversations_for_owner(
                source_anonymous_owner_id
            ),
        }

    async def merge(
        self,
        source_anonymous_owner_id: str,
        target_open_id: str,
        *,
        now: datetime | None = None,
    ) -> IdentityMergeJob:
        job = await self.auth_repository.get_or_create_merge_job(
            source_anonymous_owner_id, target_open_id, now=now
        )
        if job.status == "completed":
            return job
        await self.auth_repository.update_merge_job(
            job.id, status="running", now=now
        )
        try:
            result = await self.memory_repository.merge_user_owner(
                source_anonymous_owner_id, target_open_id
            )
            result["conversations"] = (
                await self.auth_repository.transfer_conversation_owners(
                    source_anonymous_owner_id, target_open_id, now=now
                )
            )
            await self.auth_repository.disable_anonymous_device(
                source_anonymous_owner_id,
                merged_to_open_id=target_open_id,
                now=now,
            )
            completed = await self.auth_repository.update_merge_job(
                job.id,
                status="completed",
                result=result,
                error_type=None,
                now=now,
            )
            await self.auth_repository.append_audit_event(
                actor_id=target_open_id,
                action="identity.merge.completed",
                subject_type="merge_job",
                subject_id=job.id,
                details={key: int(value) for key, value in result.items()},
                now=now,
            )
            return completed
        except Exception as exc:
            await self.auth_repository.update_merge_job(
                job.id,
                status="failed",
                error_type=type(exc).__name__,
                now=now,
            )
            raise
