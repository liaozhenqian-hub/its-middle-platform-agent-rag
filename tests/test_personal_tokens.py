import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from knowledge.auth.repository import UserAuthRepository
from knowledge.auth.tokens import PersonalTokenService


@pytest.mark.asyncio
async def test_personal_token_is_shown_once_and_only_hash_is_persisted(tmp_path):
    database = tmp_path / "auth.db"
    repository = UserAuthRepository(database)
    await repository.initialize()
    now = datetime(2026, 7, 23, 16, 0, tzinfo=UTC)
    await repository.upsert_feishu_user(
        open_id="ou_user",
        tenant_key="tenant",
        display_name="User",
        avatar_url=None,
        now=now,
    )
    service = PersonalTokenService(
        repository, secret_factory=lambda: "one-time-personal-secret"
    )

    created = await service.create(
        "ou_user",
        name="Codex laptop",
        scopes={"agent:query", "memory:read"},
        now=now,
    )
    listed = await service.list("ou_user")

    assert created.plaintext.startswith("kpat_")
    assert len(listed) == 1
    assert listed[0].name == "Codex laptop"
    assert listed[0].scopes == frozenset({"agent:query", "memory:read"})
    assert not hasattr(listed[0], "token_hash")
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT token_hash,display_prefix FROM personal_api_tokens"
        ).fetchone()
        serialized = repr(connection.execute("SELECT * FROM personal_api_tokens").fetchall())
    assert row[0] == hashlib.sha256(created.plaintext.encode()).hexdigest()
    assert row[1] == created.token.display_prefix
    assert created.plaintext not in serialized
    assert "one-time-personal-secret" not in serialized


@pytest.mark.asyncio
async def test_personal_token_authentication_updates_last_use_and_revocation_is_immediate(tmp_path):
    repository = UserAuthRepository(tmp_path / "auth.db")
    await repository.initialize()
    now = datetime(2026, 7, 23, 17, 0, tzinfo=UTC)
    await repository.upsert_feishu_user(
        open_id="ou_user",
        tenant_key="tenant",
        display_name="Token User",
        avatar_url=None,
        now=now,
    )
    service = PersonalTokenService(repository, secret_factory=lambda: "auth-secret")
    created = await service.create(
        "ou_user", name="Codex", scopes={"agent:query"}, now=now
    )

    identity = await service.authenticate(
        created.plaintext, now=now + timedelta(hours=1)
    )
    listed = await service.list("ou_user")
    await service.revoke("ou_user", created.token.id, now=now + timedelta(hours=2))
    revoked_identity = await service.authenticate(
        created.plaintext, now=now + timedelta(hours=3)
    )

    assert identity is not None
    assert identity.kind == "personal_token"
    assert identity.owner_id == "ou_user"
    assert identity.scopes == frozenset({"agent:query"})
    assert listed[0].last_used_at == now + timedelta(hours=1)
    assert revoked_identity is None


@pytest.mark.asyncio
async def test_personal_token_rejects_invalid_scopes_and_duplicate_names(tmp_path):
    repository = UserAuthRepository(tmp_path / "auth.db")
    await repository.initialize()
    now = datetime(2026, 7, 23, 18, 0, tzinfo=UTC)
    await repository.upsert_feishu_user(
        open_id="ou_user",
        tenant_key="tenant",
        display_name="User",
        avatar_url=None,
        now=now,
    )
    service = PersonalTokenService(repository)

    with pytest.raises(ValueError, match="scope"):
        await service.create("ou_user", name="Admin", scopes={"admin:write"})
    await service.create("ou_user", name="Codex", scopes={"memory:read"})
    with pytest.raises(ValueError, match="name"):
        await service.create("ou_user", name="Codex", scopes={"agent:query"})
