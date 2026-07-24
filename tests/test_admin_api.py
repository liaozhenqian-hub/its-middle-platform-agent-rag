from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from knowledge.api.app import create_app
from knowledge.catalog.auth import AdminSessionService
from knowledge.catalog.models import KnowledgeSourceCreate, SourceType
from knowledge.catalog.repository import CatalogRepository
from knowledge.config.settings import Settings


class FakeAuthenticator:
    def authenticate(self, username: str, password: str) -> bool:
        return username == "admin" and password == "correct-password"


@pytest.mark.asyncio
async def test_client_catalog_and_admin_cookie_csrf_flow(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.db")
    await repository.initialize()
    await repository.create_source(
        KnowledgeSourceCreate(
            id="git-1",
            space_id="middle-platform",
            domain_id=None,
            source_type=SourceType.GIT,
            name="中台代码",
            config={"branch": "main"},
        )
    )
    sessions = AdminSessionService(repository, ttl=timedelta(hours=8))
    settings = Settings(
        _env_file=None,
        KNOWLEDGE_CATALOG_DB=tmp_path / "catalog.db",
        ADMIN_COOKIE_SECURE=False,
    )
    app = create_app(
        agent_service=object(),
        component_status={},
        catalog_repository=repository,
        admin_authenticator=FakeAuthenticator(),
        admin_session_service=sessions,
        runtime_settings=settings,
    )

    with TestClient(app) as client:
        spaces = client.get("/api/v1/knowledge/spaces")
        assert spaces.status_code == 200
        assert spaces.json()[0]["id"] == "middle-platform"
        assert [item["id"] for item in spaces.json()[0]["domains"]] == [
            "metric-platform",
            "approval-flow",
            "workflow",
        ]

        assert client.post(
            "/api/v1/admin/auth/login",
            json={"username": "admin", "password": "wrong"},
        ).status_code == 401
        login = client.post(
            "/api/v1/admin/auth/login",
            json={"username": "admin", "password": "correct-password"},
        )
        assert login.status_code == 200
        csrf_token = login.json()["csrf_token"]
        assert csrf_token
        assert "knowledge_admin=" in login.headers["set-cookie"]
        assert "HttpOnly" in login.headers["set-cookie"]
        assert "SameSite=strict" in login.headers["set-cookie"]

        me = client.get("/api/v1/admin/auth/me")
        assert me.status_code == 200
        assert me.json()["username"] == "admin"
        assert me.json()["csrf_token"] == csrf_token

        endpoint = "/api/v1/admin/sources/git-1/sync"
        assert client.post(endpoint).status_code == 403
        queued = client.post(endpoint, headers={"X-CSRF-Token": csrf_token})
        assert queued.status_code == 202
        assert queued.json()["state"] == "queued"

        assert client.request(
            "DELETE",
            "/api/v1/admin/sources/git-1",
            headers={"X-CSRF-Token": csrf_token},
            json={"confirm_name": "wrong"},
        ).status_code == 409
        deleting = client.request(
            "DELETE",
            "/api/v1/admin/sources/git-1",
            headers={"X-CSRF-Token": csrf_token},
            json={"confirm_name": "中台代码"},
        )
        assert deleting.status_code == 202
        assert deleting.json()["kind"] == "delete"
        jobs = client.get("/api/v1/admin/jobs")
        assert jobs.status_code == 200
        assert {item["kind"] for item in jobs.json()} == {"manual", "delete"}
        assert (await repository.get_source("git-1")).enabled is False

        logout = client.post(
            "/api/v1/admin/auth/logout",
            headers={"X-CSRF-Token": csrf_token},
        )
        assert logout.status_code == 204
        assert client.get("/api/v1/admin/auth/me").status_code == 401


def test_fastapi_serves_frontend_assets_and_spa_history_fallback(tmp_path):
    frontend = tmp_path / "dist"
    assets = frontend / "assets"
    assets.mkdir(parents=True)
    (frontend / "index.html").write_text("<html>knowledge-ui</html>", encoding="utf-8")
    (assets / "app.js").write_text("window.ready=true", encoding="utf-8")
    app = create_app(
        agent_service=object(),
        component_status={},
        runtime_settings=Settings(_env_file=None, FRONTEND_DIST=frontend),
    )

    with TestClient(app) as client:
        assert "knowledge-ui" in client.get("/chat").text
        assert "knowledge-ui" in client.get("/admin/sources").text
        assert client.get("/assets/app.js").text == "window.ready=true"
        assert client.get("/health/live").json() == {"status": "live"}
