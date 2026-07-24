from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from knowledge.auth.api import create_account_router, create_auth_router
from knowledge.auth.oauth import FeishuOAuthProfile
from knowledge.auth.merge import IdentityMergeService
from knowledge.auth.repository import UserAuthRepository
from knowledge.auth.service import UserAuthService
from knowledge.api.app import create_app
from knowledge.config.settings import Settings
from knowledge.memory.repository import MemoryRepository


class _FakeOAuth:
    def __init__(self):
        self.codes: list[str] = []

    def authorization_url(self, state: str) -> str:
        return f"https://feishu.example/authorize?state={state}"

    async def authenticate(self, code: str) -> FeishuOAuthProfile:
        self.codes.append(code)
        return FeishuOAuthProfile(
            open_id="ou_user",
            tenant_key="tenant-key",
            display_name="Feishu User",
            avatar_url="https://avatar.example/user.png",
        )


@pytest.fixture
def auth_client(tmp_path):
    async def build():
        settings = Settings(
            _env_file=None,
            USER_AUTH_DB=tmp_path / "auth.db",
            FEISHU_OAUTH_ENABLED=True,
            FEISHU_APP_ID="cli_test",
            FEISHU_APP_SECRET="secret",
            FEISHU_TENANT_KEY="tenant-key",
        )
        repository = UserAuthRepository(settings.resolved_user_auth_db)
        await repository.initialize()
        memory = MemoryRepository(tmp_path / "memory.db")
        await memory.initialize()
        service = UserAuthService(
            settings,
            repository,
            oauth_client=_FakeOAuth(),
            merge_service=IdentityMergeService(repository, memory),
        )
        app = FastAPI()
        app.state.user_auth_service = service
        app.include_router(create_auth_router())
        app.include_router(create_account_router())
        return app

    import asyncio

    return asyncio.run(build())


def test_user_auth_service_uses_dedicated_feishu_oauth_application(tmp_path):
    settings = Settings(
        _env_file=None,
        USER_AUTH_DB=tmp_path / "auth.db",
        FEISHU_APP_ID="cli_bot",
        FEISHU_APP_SECRET="bot-secret",
        FEISHU_OAUTH_APP_ID="cli_oauth",
        FEISHU_OAUTH_APP_SECRET="oauth-secret",
    )
    repository = UserAuthRepository(settings.resolved_user_auth_db)

    service = UserAuthService(settings, repository)

    assert service.oauth_client is not None
    assert service.oauth_client.app_id == "cli_oauth"


def test_feishu_login_start_callback_me_and_logout(auth_client):
    with TestClient(auth_client) as client:
        started = client.get("/api/v1/auth/feishu/start", follow_redirects=False)
        assert started.status_code == 307
        assert "knowledge_anon=" in started.headers["set-cookie"]
        assert "SameSite=lax" in started.headers["set-cookie"]
        state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]

        callback = client.get(
            f"/api/v1/auth/feishu/callback?code=one-use-code&state={state}",
            follow_redirects=False,
        )
        assert callback.status_code == 303
        assert callback.headers["location"] == "/chat"
        assert "knowledge_user=" in callback.headers["set-cookie"]

        me = client.get("/api/v1/auth/me")
        assert me.status_code == 200
        payload = me.json()
        assert payload["authenticated"] is True
        assert payload["owner_id"] == "ou_user"
        assert payload["display_name"] == "Feishu User"
        assert payload["csrf_token"]
        assert payload["feishu_login_available"] is True
        assert payload["feishu_login_url"] == (
            "http://172.18.26.1:8000/api/v1/auth/feishu/start"
        )
        csrf = payload["csrf_token"]

        logged_out = client.post(
            "/api/v1/auth/logout", headers={"X-User-CSRF-Token": csrf}
        )
        assert logged_out.status_code == 204
        assert "knowledge_user=" in logged_out.headers["set-cookie"]
        assert "Max-Age=0" in logged_out.headers["set-cookie"]

        anonymous_me = client.get("/api/v1/auth/me")
        assert anonymous_me.status_code == 200
        assert anonymous_me.json()["authenticated"] is False
        assert anonymous_me.json()["identity_kind"] == "anonymous"


def test_feishu_callback_rejects_reused_state(auth_client):
    with TestClient(auth_client) as client:
        started = client.get("/api/v1/auth/feishu/start", follow_redirects=False)
        state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
        first = client.get(
            f"/api/v1/auth/feishu/callback?code=code&state={state}",
            follow_redirects=False,
        )
        second = client.get(
            f"/api/v1/auth/feishu/callback?code=code&state={state}",
            follow_redirects=False,
        )

    assert first.status_code == 303
    assert second.status_code == 400


def test_logout_requires_user_csrf(auth_client):
    with TestClient(auth_client) as client:
        started = client.get("/api/v1/auth/feishu/start", follow_redirects=False)
        state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
        client.get(
            f"/api/v1/auth/feishu/callback?code=code&state={state}",
            follow_redirects=False,
        )
        response = client.post("/api/v1/auth/logout")

    assert response.status_code == 403


def test_main_app_exposes_user_auth_routes_with_injected_service(tmp_path):
    import asyncio

    settings = Settings(
        _env_file=None,
        USER_AUTH_DB=tmp_path / "auth.db",
        FEISHU_OAUTH_ENABLED=False,
    )
    repository = UserAuthRepository(settings.resolved_user_auth_db)
    asyncio.run(repository.initialize())
    service = UserAuthService(settings, repository)
    application = create_app(
        agent_service=object(),
        runtime_settings=settings,
        user_auth_service=service,
        component_status={"user_auth": {"status": "available"}},
    )

    with TestClient(application) as client:
        me = client.get("/api/v1/auth/me")
        ready = client.get("/health/ready")

    assert me.status_code == 200
    assert me.json()["identity_kind"] == "anonymous"
    assert me.json()["feishu_login_available"] is False
    assert ready.json()["components"]["user_auth"]["status"] == "available"


def _login(client: TestClient) -> str:
    started = client.get("/api/v1/auth/feishu/start", follow_redirects=False)
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
    callback = client.get(
        f"/api/v1/auth/feishu/callback?code=code&state={state}",
        follow_redirects=False,
    )
    assert callback.status_code == 303
    return client.get("/api/v1/auth/me").json()["csrf_token"]


def test_merge_preview_and_confirm_require_feishu_session_csrf(auth_client):
    with TestClient(auth_client) as client:
        csrf = _login(client)
        preview = client.get("/api/v1/auth/merge-preview")
        forbidden = client.post(
            "/api/v1/auth/merge-anonymous", json={"confirm": True}
        )
        confirmed = client.post(
            "/api/v1/auth/merge-anonymous",
            json={"confirm": True},
            headers={"X-User-CSRF-Token": csrf},
        )
        after = client.get("/api/v1/auth/merge-preview")

    assert preview.status_code == 200
    assert preview.json()["available"] is True
    assert forbidden.status_code == 403
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "completed"
    assert after.json()["available"] is False


def test_declining_merge_keeps_anonymous_data_and_clears_prompt(auth_client):
    with TestClient(auth_client) as client:
        csrf = _login(client)
        declined = client.post(
            "/api/v1/auth/merge-anonymous",
            json={"confirm": False},
            headers={"X-User-CSRF-Token": csrf},
        )
        after = client.get("/api/v1/auth/merge-preview")

    assert declined.status_code == 200
    assert declined.json()["status"] == "declined"
    assert after.json()["available"] is False


def test_personal_token_api_shows_secret_once_and_revokes_immediately(auth_client):
    with TestClient(auth_client) as client:
        csrf = _login(client)
        created = client.post(
            "/api/v1/account/tokens",
            json={
                "name": "Codex",
                "scopes": ["agent:query", "memory:read"],
            },
            headers={"X-User-CSRF-Token": csrf},
        )
        assert created.status_code == 201
        plaintext = created.json()["token"]
        token_id = created.json()["item"]["id"]

        listed = client.get("/api/v1/account/tokens")
        bearer_me = client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {plaintext}"}
        )
        bearer_management = client.post(
            "/api/v1/account/tokens",
            json={"name": "Forbidden", "scopes": ["agent:query"]},
            headers={"Authorization": f"Bearer {plaintext}"},
        )
        revoked = client.delete(
            f"/api/v1/account/tokens/{token_id}",
            headers={"X-User-CSRF-Token": csrf},
        )
        after_revoke = client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {plaintext}"}
        )

    assert listed.status_code == 200
    assert "token" not in listed.json()[0]
    assert "token_hash" not in listed.json()[0]
    assert bearer_me.status_code == 200
    assert bearer_me.json()["identity_kind"] == "personal_token"
    assert bearer_management.status_code == 403
    assert revoked.status_code == 204
    assert after_revoke.status_code == 401


def test_personal_token_create_requires_user_csrf(auth_client):
    with TestClient(auth_client) as client:
        _login(client)
        response = client.post(
            "/api/v1/account/tokens",
            json={"name": "Codex", "scopes": ["agent:query"]},
        )

    assert response.status_code == 403
