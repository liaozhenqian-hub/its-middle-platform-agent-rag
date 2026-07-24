import base64
import hashlib
import io
from datetime import timedelta
import zipfile

import pytest
from fastapi.testclient import TestClient

from knowledge.api.app import create_app
from knowledge.catalog.auth import AdminSessionService
from knowledge.catalog.models import KnowledgeSourceCreate, SourceType, SyncJobState
from knowledge.catalog.repository import CatalogRepository
from knowledge.catalog.secrets import CatalogSecretStore, SecretCipher
from knowledge.config.settings import Settings
from knowledge.source_sync import GitLabBranch, GitLabProject


class FakeAuthenticator:
    def authenticate(self, username, password):
        return username == "admin" and password == "correct"


class FakeGitLabClient:
    async def search_projects(self, query):
        assert query == "middle"
        return [
            GitLabProject(
                id=42,
                path_with_namespace="platform/middle",
                name="middle",
                web_url="https://gitlab.example/platform/middle",
                default_branch="main",
            )
        ]

    async def list_branches(self, project_id, search=""):
        assert str(project_id) == "42"
        return [GitLabBranch(name="main", commit_sha="abc123")]


def _secret_store(repository):
    key = base64.urlsafe_b64encode(b"s" * 32).decode("ascii")
    return CatalogSecretStore(repository, SecretCipher(key))


def _document_archive(content: str) -> bytes:
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        archive.writestr("docs/guide.md", content)
    return archive_bytes.getvalue()


@pytest.mark.asyncio
async def test_admin_creates_git_source_and_webhook_is_validated_and_deduplicated(
    tmp_path,
):
    repository = CatalogRepository(tmp_path / "catalog.db")
    await repository.initialize()
    sessions = AdminSessionService(repository, ttl=timedelta(hours=8))
    settings = Settings(_env_file=None, ADMIN_COOKIE_SECURE=False)
    app = create_app(
        agent_service=object(),
        component_status={},
        catalog_repository=repository,
        admin_authenticator=FakeAuthenticator(),
        admin_session_service=sessions,
        runtime_settings=settings,
        gitlab_client=FakeGitLabClient(),
    )

    with TestClient(app) as client:
        assert client.get(
            "/api/v1/admin/gitlab/projects", params={"search": "middle"}
        ).status_code == 401
        login = client.post(
            "/api/v1/admin/auth/login",
            json={"username": "admin", "password": "correct"},
        ).json()
        headers = {"X-CSRF-Token": login["csrf_token"]}
        projects = client.get(
            "/api/v1/admin/gitlab/projects", params={"search": "middle"}
        )
        branches = client.get("/api/v1/admin/gitlab/projects/42/branches")
        assert projects.json()[0]["path_with_namespace"] == "platform/middle"
        assert branches.json()[0]["name"] == "main"

        created = client.post(
            "/api/v1/admin/sources/git",
            headers=headers,
            json={
                "name": "中台代码",
                "project_id": "42",
                "project_path": "platform/middle",
                "project_url": "https://gitlab.example/platform/middle.git",
                "project_web_url": "https://gitlab.example/platform/middle",
                "branch": "main",
                "rules": [
                    {"pattern": "**/metric/**", "domain_id": "metric-platform"},
                    {"pattern": "**/approval/**", "domain_id": "approval-flow"},
                    {"pattern": "**/workflow/**", "domain_id": "workflow"},
                ],
            },
        )
        assert created.status_code == 201
        payload = created.json()
        source_id = payload["source"]["id"]
        webhook_secret = payload["webhook_secret"]
        assert webhook_secret
        assert "webhook_secret_hash" not in str(payload)
        assert await repository.get_webhook_secret_hash(source_id) == hashlib.sha256(
            webhook_secret.encode("utf-8")
        ).hexdigest()
        stored = await repository.get_source(source_id)
        assert stored.config["project_url"] == (
            "https://gitlab.example/platform/middle.git"
        )
        assert webhook_secret not in str(stored)

        detail = client.get(f"/api/v1/admin/sources/{source_id}")
        assert detail.status_code == 200
        assert len(detail.json()["rules"]) == 3
        replaced = client.put(
            f"/api/v1/admin/sources/{source_id}/rules",
            headers=headers,
            json={
                "rules": [
                    {
                        "pattern": "backend/metric/**",
                        "domain_id": "metric-platform",
                        "priority": 10,
                    }
                ]
            },
        )
        assert replaced.status_code == 200
        assert replaced.json()[0]["pattern"] == "backend/metric/**"

        webhook_body = {
            "ref": "refs/heads/main",
            "after": "def456",
            "project": {"id": 42},
        }
        url = f"/api/v1/webhooks/gitlab/{source_id}"
        assert client.post(url, json=webhook_body).status_code == 403
        first = client.post(
            url,
            json=webhook_body,
            headers={"X-Gitlab-Token": webhook_secret},
        )
        second = client.post(
            url,
            json=webhook_body,
            headers={"X-Gitlab-Token": webhook_secret},
        )
        assert first.status_code == 202
        assert second.status_code == 202
        assert first.json()["id"] == second.json()["id"]
        assert first.json()["target_commit"] == "def456"


@pytest.mark.asyncio
async def test_gitlab_webhook_accepts_legacy_encrypted_secret_hash(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.db")
    await repository.initialize()
    await repository.create_source(
        KnowledgeSourceCreate(
            id="legacy-git",
            space_id="middle-platform",
            domain_id=None,
            source_type=SourceType.GIT,
            name="Legacy Git source",
            config={"project_id": "42", "branch": "main"},
        )
    )
    secret_store = _secret_store(repository)
    webhook_secret = "legacy-webhook-secret"
    await secret_store.set(
        "legacy-git",
        "webhook_secret_hash",
        hashlib.sha256(webhook_secret.encode("utf-8")).hexdigest(),
    )
    app = create_app(
        agent_service=object(),
        component_status={},
        catalog_repository=repository,
        runtime_settings=Settings(_env_file=None),
        catalog_secret_store=secret_store,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/webhooks/gitlab/legacy-git",
            headers={"X-Gitlab-Token": webhook_secret},
            json={
                "ref": "refs/heads/main",
                "after": "abc123",
                "project": {"id": 42},
            },
        )

    assert response.status_code == 202
    assert response.json()["target_commit"] == "abc123"
    assert await repository.get_webhook_secret_hash("legacy-git") is None


@pytest.mark.asyncio
async def test_swagger_source_credentials_are_encrypted_and_never_returned(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.db")
    await repository.initialize()
    sessions = AdminSessionService(repository)
    secrets = _secret_store(repository)
    app = create_app(
        agent_service=object(),
        component_status={},
        catalog_repository=repository,
        admin_authenticator=FakeAuthenticator(),
        admin_session_service=sessions,
        runtime_settings=Settings(
            _env_file=None,
            ADMIN_COOKIE_SECURE=False,
            SWAGGER_ALLOWED_HOSTS="swagger.internal",
        ),
        catalog_secret_store=secrets,
    )

    with TestClient(app) as client:
        login = client.post(
            "/api/v1/admin/auth/login",
            json={"username": "admin", "password": "correct"},
        ).json()
        created = client.post(
            "/api/v1/admin/sources/swagger",
            headers={"X-CSRF-Token": login["csrf_token"]},
            json={
                "name": "指标 Swagger",
                "domain_id": "metric-platform",
                "url": "https://swagger.internal/openapi.json",
                "auth_type": "bearer",
                "bearer_token": "swagger-secret-token",
            },
        )
        assert created.status_code == 201
        serialized = str(created.json())
        assert "swagger-secret-token" not in serialized
        assert created.json()["credential_configured"] is True
        listed = client.get("/api/v1/admin/sources")
        assert "swagger-secret-token" not in str(listed.json())
        source_id = created.json()["id"]
        assert await secrets.get(source_id, "bearer_token") == "swagger-secret-token"

        rejected = client.post(
            "/api/v1/admin/sources/swagger",
            headers={"X-CSRF-Token": login["csrf_token"]},
            json={
                "name": "Bad",
                "domain_id": "metric-platform",
                "url": "http://169.254.169.254/latest/meta-data",
                "auth_type": "none",
            },
        )
        assert rejected.status_code == 422


@pytest.mark.asyncio
async def test_document_zip_is_validated_stored_and_queued_as_one_version(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.db")
    await repository.initialize()
    sessions = AdminSessionService(repository)
    app = create_app(
        agent_service=object(),
        component_status={},
        catalog_repository=repository,
        admin_authenticator=FakeAuthenticator(),
        admin_session_service=sessions,
        runtime_settings=Settings(
            _env_file=None,
            ADMIN_COOKIE_SECURE=False,
            KNOWLEDGE_STORAGE_ROOT=tmp_path / "storage",
            UPLOAD_MAX_FILE_BYTES=1024 * 1024,
            UPLOAD_MAX_BATCH_BYTES=2 * 1024 * 1024,
            UPLOAD_MAX_FILES=20,
        ),
    )
    safe_archive = io.BytesIO()
    with zipfile.ZipFile(safe_archive, "w") as archive:
        archive.writestr("docs/guide.md", "# 指标口径\n销售额定义。")
    unsafe_archive = io.BytesIO()
    with zipfile.ZipFile(unsafe_archive, "w") as archive:
        archive.writestr("../secret.md", "bad")

    with TestClient(app) as client:
        login = client.post(
            "/api/v1/admin/auth/login",
            json={"username": "admin", "password": "correct"},
        ).json()
        headers = {"X-CSRF-Token": login["csrf_token"]}
        created = client.post(
            "/api/v1/admin/sources/documents",
            headers=headers,
            data={"name": "指标产品文档", "domain_id": "metric-platform", "version": "v1"},
            files={"upload": ("documents.zip", safe_archive.getvalue(), "application/zip")},
        )

        assert created.status_code == 202
        source_id = created.json()["source"]["id"]
        initial_job_id = created.json()["job"]["id"]
        stored_path = tmp_path / "storage" / "uploads" / source_id / "v1" / "docs" / "guide.md"
        assert stored_path.exists()
        assert created.json()["job"]["kind"] == "document"
        claimed = await repository.claim_next_job("test-worker")
        assert claimed.id == initial_job_id
        await repository.complete_job(
            claimed.id,
            worker_id="test-worker",
            attempt=claimed.attempt,
        )

        v2_archive = io.BytesIO()
        with zipfile.ZipFile(v2_archive, "w") as archive:
            archive.writestr("docs/guide.md", "# 指标口径 v2\n新定义。")
        upgraded = client.post(
            f"/api/v1/admin/sources/{source_id}/documents/versions",
            headers=headers,
            data={"version": "v2"},
            files={"upload": ("documents.zip", v2_archive.getvalue(), "application/zip")},
        )
        assert upgraded.status_code == 202
        assert stored_path.exists()
        assert (
            tmp_path / "storage" / "uploads" / source_id / "v2" / "docs" / "guide.md"
        ).exists()
        assert (await repository.get_source(source_id)).config["pending_version"] == "v2"

        rejected = client.post(
            "/api/v1/admin/sources/documents",
            headers=headers,
            data={"name": "Bad", "domain_id": "metric-platform", "version": "v1"},
            files={"upload": ("bad.zip", unsafe_archive.getvalue(), "application/zip")},
        )
        assert rejected.status_code == 422
        sources = await repository.list_sources(source_type=None)
        assert [item.id for item in sources] == [source_id]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_method", ["enqueue_job", "append_audit_event"])
async def test_document_source_creation_rolls_back_catalog_and_files_on_failure(
    tmp_path, monkeypatch, failure_method
):
    repository = CatalogRepository(tmp_path / "catalog.db")
    await repository.initialize()
    sessions = AdminSessionService(repository)
    storage_root = tmp_path / "storage"
    app = create_app(
        agent_service=object(),
        component_status={},
        catalog_repository=repository,
        admin_authenticator=FakeAuthenticator(),
        admin_session_service=sessions,
        runtime_settings=Settings(
            _env_file=None,
            ADMIN_COOKIE_SECURE=False,
            KNOWLEDGE_STORAGE_ROOT=storage_root,
        ),
    )

    async def fail_after_source_creation(*args, **kwargs):
        raise RuntimeError(f"injected {failure_method} failure")

    monkeypatch.setattr(repository, failure_method, fail_after_source_creation)

    with TestClient(app) as client:
        login = client.post(
            "/api/v1/admin/auth/login",
            json={"username": "admin", "password": "correct"},
        ).json()
        with pytest.raises(RuntimeError, match=f"injected {failure_method} failure"):
            client.post(
                "/api/v1/admin/sources/documents",
                headers={"X-CSRF-Token": login["csrf_token"]},
                data={
                    "name": "Metric documentation",
                    "domain_id": "metric-platform",
                    "version": "v1",
                },
                files={
                    "upload": (
                        "documents.zip",
                        _document_archive("# Metric guide"),
                        "application/zip",
                    )
                },
            )

    assert await repository.list_sources(source_type=None) == []
    uploads_root = storage_root / "uploads"
    assert not any(
        child.name != ".staging" for child in uploads_root.iterdir()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_method", ["enqueue_job", "append_audit_event"])
async def test_document_version_upload_restores_config_and_removes_files_on_failure(
    tmp_path, monkeypatch, failure_method
):
    repository = CatalogRepository(tmp_path / "catalog.db")
    await repository.initialize()
    sessions = AdminSessionService(repository)
    storage_root = tmp_path / "storage"
    app = create_app(
        agent_service=object(),
        component_status={},
        catalog_repository=repository,
        admin_authenticator=FakeAuthenticator(),
        admin_session_service=sessions,
        runtime_settings=Settings(
            _env_file=None,
            ADMIN_COOKIE_SECURE=False,
            KNOWLEDGE_STORAGE_ROOT=storage_root,
        ),
    )

    with TestClient(app) as client:
        login = client.post(
            "/api/v1/admin/auth/login",
            json={"username": "admin", "password": "correct"},
        ).json()
        headers = {"X-CSRF-Token": login["csrf_token"]}
        created = client.post(
            "/api/v1/admin/sources/documents",
            headers=headers,
            data={
                "name": "Metric documentation",
                "domain_id": "metric-platform",
                "version": "v1",
            },
            files={
                "upload": (
                    "documents.zip",
                    _document_archive("# Metric guide v1"),
                    "application/zip",
                )
            },
        )
        source_id = created.json()["source"]["id"]
        initial_job = await repository.claim_next_job("test-worker")
        await repository.complete_job(
            initial_job.id,
            worker_id="test-worker",
            attempt=initial_job.attempt,
        )
        original_source = await repository.get_source(source_id)

        async def fail_after_source_update(*args, **kwargs):
            raise RuntimeError(f"injected {failure_method} failure")

        monkeypatch.setattr(repository, failure_method, fail_after_source_update)

        with pytest.raises(RuntimeError, match=f"injected {failure_method} failure"):
            client.post(
                f"/api/v1/admin/sources/{source_id}/documents/versions",
                headers=headers,
                data={"version": "v2"},
                files={
                    "upload": (
                        "documents.zip",
                        _document_archive("# Metric guide v2"),
                        "application/zip",
                    )
                },
            )

    restored_source = await repository.get_source(source_id)
    assert restored_source.config == original_source.config
    assert all(
        job.state is not SyncJobState.QUEUED
        for job in await repository.list_jobs(source_id=source_id)
    )
    assert (storage_root / "uploads" / source_id / "v1").is_dir()
    assert not (storage_root / "uploads" / source_id / "v2").exists()
