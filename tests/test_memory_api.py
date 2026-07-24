from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from knowledge.api.app import create_app
from knowledge.auth.identity import hash_secret
from knowledge.auth.repository import UserAuthRepository
from knowledge.auth.service import UserAuthService
from knowledge.catalog.auth import AdminSessionService
from knowledge.catalog.repository import CatalogRepository
from knowledge.config.settings import Settings
from knowledge.memory.models import MemoryCandidateCreate
from knowledge.memory.repository import MemoryRepository
from knowledge.memory.service import MemoryService


class FakeAuthenticator:
    def authenticate(self, username: str, password: str) -> bool:
        return username == "admin" and password == "correct-password"


@pytest.mark.asyncio
async def test_admin_reviews_only_domain_memory_and_user_can_only_forget_own_memory(tmp_path):
    catalog = CatalogRepository(tmp_path / "catalog.db")
    await catalog.initialize()
    sessions = AdminSessionService(catalog, ttl=timedelta(hours=8))
    repository = MemoryRepository(tmp_path / "memory.db")
    await repository.initialize()
    service = MemoryService(repository)
    settings = Settings(
        _env_file=None,
        KNOWLEDGE_CATALOG_DB=tmp_path / "catalog.db",
        USER_AUTH_DB=tmp_path / "auth.db",
        ADMIN_COOKIE_SECURE=False,
    )
    auth_repository = UserAuthRepository(settings.resolved_user_auth_db)
    await auth_repository.initialize()
    now = datetime.now(UTC)
    for user_id, session_secret in (
        ("user-1", "session-one"),
        ("user-2", "session-two"),
    ):
        await auth_repository.upsert_feishu_user(
            open_id=user_id,
            tenant_key="tenant",
            display_name=user_id,
            avatar_url=None,
            now=now,
        )
        await auth_repository.create_user_session(
            token_hash=hash_secret(session_secret),
            open_id=user_id,
            csrf_token=f"csrf-{user_id}",
            source_anonymous_owner_id=None,
            sliding_expires_at=now + timedelta(days=7),
            absolute_expires_at=now + timedelta(days=30),
            now=now,
        )
    user_auth_service = UserAuthService(settings, auth_repository)
    candidate = await repository.create_candidate(MemoryCandidateCreate(
        scope_type="user",
        owner_id="user-1",
        space_id="middle-platform",
        domain_id="approval-flow",
        memory_type="user_preference",
        subject="answer-format",
        normalized_fact="回答接口问题包含入参与出参",
        summary="用户偏好接口回答包含入参与出参",
        source_turn_id="turn-1",
        source_citations=("chunk-1",),
        confidence=0.9,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    ))
    domain_candidate = await repository.create_candidate(MemoryCandidateCreate(
        scope_type="domain", owner_id="approval-flow", space_id="middle-platform",
        domain_id="approval-flow", memory_type="procedural_memory",
        subject="bug-playbook", normalized_fact="审批流排障流程",
        summary="审批流领域排障流程", source_turn_id="turn-domain",
        source_citations=("code-1",), confidence=0.9,
        expires_at=datetime.now(UTC) + timedelta(days=90),
    ))
    app = create_app(
        agent_service=object(),
        component_status={},
        catalog_repository=catalog,
        admin_authenticator=FakeAuthenticator(),
        admin_session_service=sessions,
        runtime_settings=settings,
        memory_service=service,
        memory_repository=repository,
        user_auth_service=user_auth_service,
    )

    with TestClient(app) as client:
        login = client.post(
            "/api/v1/admin/auth/login",
            json={"username": "admin", "password": "correct-password"},
        )
        csrf = login.json()["csrf_token"]
        listed = client.get("/api/v1/admin/memory/candidates?status=candidate")
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()] == [domain_candidate.id]

        personal_approval = client.post(
            f"/api/v1/admin/memory/candidates/{candidate.id}/approve",
            headers={"X-CSRF-Token": csrf},
        )
        assert personal_approval.status_code == 404
        approved = client.post(
            f"/api/v1/admin/memory/candidates/{domain_candidate.id}/approve",
            headers={"X-CSRF-Token": csrf},
        )
        assert approved.status_code == 200
        assert approved.json()["status"] == "confirmed"

        statistics = client.get("/api/v1/admin/memory/personal-statistics")
        assert statistics.status_code == 200
        assert statistics.json()["candidate"]["user_preference"] == 1
        assert "user-1" not in statistics.text

        client.cookies.set(settings.user_session_cookie_name, "session-one")
        personal_confirmed = client.post(
            f"/api/v1/memory/candidates/{candidate.id}/confirm",
            headers={"X-User-CSRF-Token": "csrf-user-1"},
        )
        assert personal_confirmed.status_code == 200
        own = client.get(
            "/api/v1/memory",
            headers={"X-Authenticated-User-ID": "user-2"},
        )
        assert [item["id"] for item in own.json()] == [candidate.id]
        client.cookies.set(settings.user_session_cookie_name, "session-two")
        assert client.get("/api/v1/memory").json() == []
        assert client.delete(
            f"/api/v1/memory/{candidate.id}"
        ).status_code == 404
        client.cookies.set(settings.user_session_cookie_name, "session-one")
        assert client.delete(
            f"/api/v1/memory/{candidate.id}"
        ).status_code == 204


@pytest.mark.asyncio
async def test_user_can_list_and_confirm_only_own_user_memory_candidates(tmp_path):
    repository = MemoryRepository(tmp_path / "memory.db")
    await repository.initialize()
    service = MemoryService(repository)
    settings = Settings(
        _env_file=None,
        USER_AUTH_DB=tmp_path / "auth.db",
        ADMIN_COOKIE_SECURE=False,
    )
    auth_repository = UserAuthRepository(settings.resolved_user_auth_db)
    await auth_repository.initialize()
    now = datetime.now(UTC)
    for user_id, session_secret in (
        ("user-1", "session-one"),
        ("user-2", "session-two"),
    ):
        await auth_repository.upsert_feishu_user(
            open_id=user_id,
            tenant_key="tenant",
            display_name=user_id,
            avatar_url=None,
            now=now,
        )
        await auth_repository.create_user_session(
            token_hash=hash_secret(session_secret),
            open_id=user_id,
            csrf_token=f"csrf-{user_id}",
            source_anonymous_owner_id=None,
            sliding_expires_at=now + timedelta(days=7),
            absolute_expires_at=now + timedelta(days=30),
            now=now,
        )
    own = await repository.create_candidate(MemoryCandidateCreate(
        scope_type="user",
        owner_id="user-1",
        space_id="middle-platform",
        domain_id="approval-flow",
        memory_type="user_preference",
        subject="answer-format",
        normalized_fact="回答接口问题时包含入参和出参",
        summary="偏好完整接口契约",
        source_turn_id="turn-1",
        confidence=0.9,
        expires_at=now + timedelta(days=1),
    ))
    other = await repository.create_candidate(MemoryCandidateCreate(
        scope_type="user",
        owner_id="user-2",
        space_id="middle-platform",
        domain_id=None,
        memory_type="user_context",
        subject="other-user",
        normalized_fact="其他用户上下文",
        summary="不可见",
        source_turn_id="turn-2",
        confidence=0.8,
        expires_at=now + timedelta(days=1),
    ))
    domain = await repository.create_candidate(MemoryCandidateCreate(
        scope_type="domain",
        owner_id="approval-flow",
        space_id="middle-platform",
        domain_id="approval-flow",
        memory_type="decision_memory",
        subject="domain-decision",
        normalized_fact="领域决策",
        summary="需要管理员审核",
        source_turn_id="turn-3",
        confidence=0.8,
        expires_at=now + timedelta(days=1),
    ))
    rejected_own = await repository.create_candidate(MemoryCandidateCreate(
        scope_type="user", owner_id="user-1", space_id="middle-platform",
        domain_id=None, memory_type="user_context", subject="temporary-context",
        normalized_fact="临时上下文", summary="用户决定不保留",
        source_turn_id="turn-4", confidence=0.7,
        expires_at=now + timedelta(days=1),
    ))
    app = create_app(
        agent_service=object(),
        component_status={},
        runtime_settings=settings,
        memory_service=service,
        memory_repository=repository,
        user_auth_service=UserAuthService(settings, auth_repository),
    )

    with TestClient(app) as client:
        client.cookies.set(settings.user_session_cookie_name, "session-one")
        listed = client.get("/api/v1/memory/candidates")
        no_csrf = client.post(f"/api/v1/memory/candidates/{own.id}/confirm")
        confirmed = client.post(
            f"/api/v1/memory/candidates/{own.id}/confirm",
            headers={"X-User-CSRF-Token": "csrf-user-1"},
        )
        other_result = client.post(
            f"/api/v1/memory/candidates/{other.id}/confirm",
            headers={"X-User-CSRF-Token": "csrf-user-1"},
        )
        domain_result = client.post(
            f"/api/v1/memory/candidates/{domain.id}/confirm",
            headers={"X-User-CSRF-Token": "csrf-user-1"},
        )
        rejected = client.post(
            f"/api/v1/memory/candidates/{rejected_own.id}/reject",
            headers={"X-User-CSRF-Token": "csrf-user-1"},
        )

    assert listed.status_code == 200
    assert {item["id"] for item in listed.json()} == {own.id, rejected_own.id}
    assert no_csrf.status_code == 403
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"
    assert other_result.status_code == 404
    assert domain_result.status_code == 404
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
