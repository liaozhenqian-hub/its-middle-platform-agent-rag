from datetime import UTC, datetime, timedelta

import pytest

from knowledge.auth.merge import IdentityMergeService
from knowledge.auth.repository import UserAuthRepository
from knowledge.memory.models import MemoryCandidateCreate
from knowledge.memory.repository import MemoryRepository


@pytest.mark.asyncio
async def test_identity_merge_previews_then_moves_memory_and_conversations(tmp_path):
    auth = UserAuthRepository(tmp_path / "auth.db")
    memory = MemoryRepository(tmp_path / "memory.db")
    await auth.initialize()
    await memory.initialize()
    now = datetime(2026, 7, 23, 14, 0, tzinfo=UTC)
    anonymous = await auth.create_anonymous_device(
        token_hash="anon-hash", expires_at=now + timedelta(days=1), now=now
    )
    await auth.upsert_feishu_user(
        open_id="ou_target",
        tenant_key="tenant",
        display_name="Target",
        avatar_url=None,
        now=now,
    )
    await auth.bind_conversation_owner(
        "conversation-1", anonymous.owner_id, channel="web", now=now
    )
    candidate = await memory.create_candidate(
        MemoryCandidateCreate(
            scope_type="user",
            owner_id=anonymous.owner_id,
            space_id="middle-platform",
            domain_id=None,
            memory_type="user_context",
            subject="branch",
            normalized_fact="develop",
            summary="develop",
            source_turn_id=None,
            confidence=0.9,
        )
    )
    await memory.approve_candidate(candidate.id)
    service = IdentityMergeService(auth, memory)

    preview = await service.preview(anonymous.owner_id, "ou_target")
    before = await memory.list_memories(
        scope_type="user", owner_id=anonymous.owner_id
    )
    completed = await service.merge(anonymous.owner_id, "ou_target", now=now)
    repeated = await service.merge(anonymous.owner_id, "ou_target", now=now)

    assert preview["conversations"] == 1
    assert preview["memories"] == 1
    assert len(before) == 1
    assert completed.status == "completed"
    assert repeated.id == completed.id
    assert repeated.result == completed.result
    assert (await auth.get_conversation_owner("conversation-1")).owner_id == "ou_target"
    assert await auth.get_active_anonymous_device("anon-hash", now=now) is None
    assert len(await memory.list_memories(scope_type="user", owner_id="ou_target")) == 1


class _FailOnceMemoryRepository:
    def __init__(self, delegate: MemoryRepository):
        self.delegate = delegate
        self.failed = False

    async def preview_user_owner_merge(self, source: str, target: str):
        return await self.delegate.preview_user_owner_merge(source, target)

    async def merge_user_owner(self, source: str, target: str):
        if not self.failed:
            self.failed = True
            raise RuntimeError("sensitive failure details")
        return await self.delegate.merge_user_owner(source, target)


@pytest.mark.asyncio
async def test_identity_merge_failure_keeps_device_active_and_retry_is_safe(tmp_path):
    auth = UserAuthRepository(tmp_path / "auth.db")
    memory = MemoryRepository(tmp_path / "memory.db")
    await auth.initialize()
    await memory.initialize()
    now = datetime(2026, 7, 23, 15, 0, tzinfo=UTC)
    anonymous = await auth.create_anonymous_device(
        token_hash="retry-hash", expires_at=now + timedelta(days=1), now=now
    )
    await auth.upsert_feishu_user(
        open_id="ou_retry",
        tenant_key="tenant",
        display_name="Retry",
        avatar_url=None,
        now=now,
    )
    service = IdentityMergeService(auth, _FailOnceMemoryRepository(memory))

    with pytest.raises(RuntimeError):
        await service.merge(anonymous.owner_id, "ou_retry", now=now)
    assert await auth.get_active_anonymous_device("retry-hash", now=now) is not None

    completed = await service.merge(anonymous.owner_id, "ou_retry", now=now)

    assert completed.status == "completed"
    assert completed.error_type is None
    assert await auth.get_active_anonymous_device("retry-hash", now=now) is None
