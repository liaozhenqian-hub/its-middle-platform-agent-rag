import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from knowledge.auth.identity import (
    AnonymousIdentityService,
    AuthenticationError,
    AuthorizationError,
    RequestIdentityResolver,
    require_scope,
)
from knowledge.auth.models import ResolvedIdentity
from knowledge.auth.ownership import (
    ConversationNotFoundError,
    ConversationOwnershipService,
)
from knowledge.auth.repository import UserAuthRepository


@pytest.mark.asyncio
async def test_anonymous_identity_issues_secret_but_persists_only_hash(tmp_path):
    database = tmp_path / "auth.db"
    repository = UserAuthRepository(database)
    await repository.initialize()
    now = datetime(2026, 7, 23, 7, 0, tzinfo=UTC)
    service = AnonymousIdentityService(
        repository,
        ttl_seconds=180 * 24 * 3600,
        token_factory=lambda: "plain-anonymous-secret",
    )

    resolution = await service.resolve(None, now=now)

    assert resolution.identity.kind == "anonymous"
    assert resolution.identity.owner_id.startswith("anon:")
    assert resolution.cookie_value == "plain-anonymous-secret"
    with sqlite3.connect(database) as connection:
        stored_hash = connection.execute(
            "SELECT token_hash FROM anonymous_devices"
        ).fetchone()[0]
        serialized = repr(connection.execute("SELECT * FROM anonymous_devices").fetchall())
    assert stored_hash == hashlib.sha256(b"plain-anonymous-secret").hexdigest()
    assert "plain-anonymous-secret" not in serialized


@pytest.mark.asyncio
async def test_anonymous_identity_reuses_owner_and_slides_expiry(tmp_path):
    repository = UserAuthRepository(tmp_path / "auth.db")
    await repository.initialize()
    start = datetime(2026, 7, 23, 8, 0, tzinfo=UTC)
    service = AnonymousIdentityService(
        repository,
        ttl_seconds=180 * 24 * 3600,
        token_factory=lambda: "stable-device-secret",
    )
    first = await service.resolve(None, now=start)

    second = await service.resolve(
        "stable-device-secret", now=start + timedelta(days=30)
    )
    device = await repository.get_anonymous_device_by_owner(first.identity.owner_id)

    assert second.identity.owner_id == first.identity.owner_id
    assert second.cookie_value == "stable-device-secret"
    assert device.expires_at == start + timedelta(days=210)


@pytest.mark.asyncio
async def test_anonymous_identity_uses_atomic_refresh_for_existing_cookie(tmp_path):
    repository = UserAuthRepository(tmp_path / "auth.db")
    await repository.initialize()
    now = datetime(2026, 7, 23, 8, 0, tzinfo=UTC)
    created = await repository.create_anonymous_device(
        token_hash=hashlib.sha256(b"existing-cookie").hexdigest(),
        expires_at=now + timedelta(days=1),
        now=now,
    )
    refresh_calls = []

    async def refresh(token_hash, *, expires_at, now=None):
        refresh_calls.append((token_hash, expires_at, now))
        return created

    async def legacy_lookup(*args, **kwargs):
        raise AssertionError("legacy lookup/touch path should not be used")

    repository.refresh_active_anonymous_device = refresh
    repository.get_active_anonymous_device = legacy_lookup
    repository.touch_anonymous_device = legacy_lookup
    service = AnonymousIdentityService(repository, ttl_seconds=30 * 24 * 3600)

    resolution = await service.resolve("existing-cookie", now=now)

    assert resolution.identity.owner_id == created.owner_id
    assert len(refresh_calls) == 1


@pytest.mark.asyncio
async def test_disabled_or_unknown_anonymous_cookie_gets_fresh_identity(tmp_path):
    repository = UserAuthRepository(tmp_path / "auth.db")
    await repository.initialize()
    now = datetime(2026, 7, 23, 9, 0, tzinfo=UTC)
    secrets = iter(["first-secret", "second-secret"])
    service = AnonymousIdentityService(
        repository,
        ttl_seconds=60,
        token_factory=lambda: next(secrets),
    )
    first = await service.resolve(None, now=now)
    await repository.disable_anonymous_device(first.identity.owner_id, now=now)

    second = await service.resolve("first-secret", now=now + timedelta(seconds=1))

    assert second.identity.owner_id != first.identity.owner_id
    assert second.cookie_value == "second-secret"


class _TokenAuthenticator:
    def __init__(self, identity: ResolvedIdentity | None):
        self.identity = identity
        self.seen: list[str] = []

    async def authenticate(self, token: str, *, now=None):
        self.seen.append(token)
        return self.identity


@pytest.mark.asyncio
async def test_request_identity_prefers_bearer_over_session_and_anonymous(tmp_path):
    repository = UserAuthRepository(tmp_path / "auth.db")
    await repository.initialize()
    now = datetime(2026, 7, 23, 10, 0, tzinfo=UTC)
    anonymous_service = AnonymousIdentityService(
        repository, ttl_seconds=3600, token_factory=lambda: "new-anon"
    )
    token_identity = ResolvedIdentity(
        owner_id="ou_token",
        kind="personal_token",
        display_name="Token User",
        scopes=frozenset({"agent:query"}),
        token_id="token-id",
    )
    authenticator = _TokenAuthenticator(token_identity)
    resolver = RequestIdentityResolver(
        repository,
        anonymous_service,
        token_authenticator=authenticator,
        session_sliding_ttl_seconds=7 * 24 * 3600,
    )

    resolution = await resolver.resolve(
        authorization="Bearer personal-secret",
        user_session_cookie="ignored-session",
        anonymous_cookie="ignored-anon",
        now=now,
    )

    assert resolution.identity == token_identity
    assert authenticator.seen == ["personal-secret"]
    assert resolution.cookie_value is None


@pytest.mark.asyncio
async def test_request_identity_uses_active_feishu_session_and_slides_it(tmp_path):
    repository = UserAuthRepository(tmp_path / "auth.db")
    await repository.initialize()
    now = datetime(2026, 7, 23, 11, 0, tzinfo=UTC)
    await repository.upsert_feishu_user(
        open_id="ou_session",
        tenant_key="tenant",
        display_name="Feishu User",
        avatar_url=None,
        now=now,
    )
    session = await repository.create_user_session(
        token_hash=hashlib.sha256(b"session-secret").hexdigest(),
        open_id="ou_session",
        csrf_token="csrf",
        source_anonymous_owner_id=None,
        sliding_expires_at=now + timedelta(days=1),
        absolute_expires_at=now + timedelta(days=30),
        now=now,
    )
    resolver = RequestIdentityResolver(
        repository,
        AnonymousIdentityService(repository, ttl_seconds=3600),
        session_sliding_ttl_seconds=7 * 24 * 3600,
    )

    resolution = await resolver.resolve(
        authorization=None,
        user_session_cookie="session-secret",
        anonymous_cookie=None,
        now=now + timedelta(hours=1),
    )
    touched = await repository.get_user_session(session.id)

    assert resolution.identity.kind == "feishu"
    assert resolution.identity.owner_id == "ou_session"
    assert resolution.identity.csrf_token == "csrf"
    assert touched.sliding_expires_at == now + timedelta(days=7, hours=1)


@pytest.mark.asyncio
async def test_invalid_bearer_fails_without_cookie_fallback(tmp_path):
    repository = UserAuthRepository(tmp_path / "auth.db")
    await repository.initialize()
    resolver = RequestIdentityResolver(
        repository,
        AnonymousIdentityService(
            repository, ttl_seconds=3600, token_factory=lambda: "must-not-run"
        ),
        token_authenticator=_TokenAuthenticator(None),
        session_sliding_ttl_seconds=3600,
    )

    with pytest.raises(AuthenticationError):
        await resolver.resolve(
            authorization="Bearer invalid",
            user_session_cookie=None,
            anonymous_cookie=None,
        )

    with sqlite3.connect(tmp_path / "auth.db") as connection:
        count = connection.execute("SELECT COUNT(*) FROM anonymous_devices").fetchone()[0]
    assert count == 0


@pytest.mark.asyncio
async def test_expired_session_falls_back_to_anonymous_and_requests_cookie_clear(tmp_path):
    repository = UserAuthRepository(tmp_path / "auth.db")
    await repository.initialize()
    now = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)
    await repository.upsert_feishu_user(
        open_id="ou_expired",
        tenant_key="tenant",
        display_name="Expired User",
        avatar_url=None,
        now=now - timedelta(days=2),
    )
    await repository.create_user_session(
        token_hash=hashlib.sha256(b"expired-session").hexdigest(),
        open_id="ou_expired",
        csrf_token="csrf",
        source_anonymous_owner_id=None,
        sliding_expires_at=now - timedelta(days=1),
        absolute_expires_at=now + timedelta(days=1),
        now=now - timedelta(days=2),
    )
    resolver = RequestIdentityResolver(
        repository,
        AnonymousIdentityService(
            repository, ttl_seconds=3600, token_factory=lambda: "fallback-anon"
        ),
        session_sliding_ttl_seconds=3600,
    )

    resolution = await resolver.resolve(
        authorization=None,
        user_session_cookie="expired-session",
        anonymous_cookie=None,
        now=now,
    )

    assert resolution.identity.kind == "anonymous"
    assert resolution.clear_user_cookie is True


def test_scope_guard_forbids_missing_personal_token_scope():
    identity = ResolvedIdentity(
        owner_id="ou_limited",
        kind="personal_token",
        display_name="Limited",
        scopes=frozenset({"memory:read"}),
    )

    require_scope(identity, "memory:read")
    with pytest.raises(AuthorizationError):
        require_scope(identity, "agent:query")


@pytest.mark.asyncio
async def test_conversation_ownership_service_hides_cross_owner_bindings(tmp_path):
    repository = UserAuthRepository(tmp_path / "auth.db")
    await repository.initialize()
    service = ConversationOwnershipService(repository)
    now = datetime(2026, 7, 23, 13, 0, tzinfo=UTC)

    await service.claim("conversation", "ou_owner", channel="feishu", now=now)
    await service.claim("conversation", "ou_owner", channel="codex", now=now)

    with pytest.raises(ConversationNotFoundError):
        await service.claim("conversation", "ou_other", channel="web", now=now)
    with pytest.raises(ConversationNotFoundError):
        await service.release("conversation", "ou_other")

    assert await service.release("conversation", "ou_owner") is True
    assert await service.release("conversation", "ou_owner") is False
