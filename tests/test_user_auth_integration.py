import asyncio
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from knowledge.agent_runtime.service import AgentRunResponse
from knowledge.api.app import create_app
from knowledge.auth.repository import UserAuthRepository
from knowledge.auth.service import UserAuthService
from knowledge.config.settings import Settings
from knowledge.memory.models import MemoryCandidateCreate
from knowledge.memory.repository import MemoryRepository
from knowledge.memory.service import MemoryService


class _Agent:
    def __init__(self):
        self.calls = []
        self.deleted = []

    async def chat(self, message, conversation_id, **kwargs):
        self.calls.append((conversation_id, kwargs.get("user_id")))
        return AgentRunResponse(
            status="completed",
            conversation_id=conversation_id,
            run_id=kwargs["run_id"],
            answer="ok",
            last_agent="Manager",
            citations=[],
            tool_runs=[],
            approvals=[],
        )

    async def prepare_conversation_scope(self, conversation_id=None, **kwargs):
        return conversation_id or "generated-conversation"

    async def stream_chat(self, message, conversation_id, **kwargs):
        yield {
            "event": "run.completed",
            "data": (
                await self.chat(message, conversation_id, **kwargs)
            ).to_dict(),
        }

    async def delete_conversation(self, conversation_id):
        self.deleted.append(conversation_id)


def _build(tmp_path):
    async def initialize():
        settings = Settings(
            _env_file=None,
            USER_AUTH_DB=tmp_path / "auth.db",
            MEMORY_DB=tmp_path / "memory.db",
        )
        auth_repository = UserAuthRepository(settings.resolved_user_auth_db)
        memory_repository = MemoryRepository(settings.resolved_memory_db)
        await auth_repository.initialize()
        await memory_repository.initialize()
        auth_service = UserAuthService(settings, auth_repository)
        agent = _Agent()
        app = create_app(
            agent_service=agent,
            runtime_settings=settings,
            user_auth_service=auth_service,
            memory_repository=memory_repository,
            memory_service=MemoryService(memory_repository),
            component_status={"user_auth": {"status": "available"}},
        )
        return app, agent, memory_repository

    return asyncio.run(initialize())


def test_chat_uses_server_identity_ignores_forged_header_and_enforces_owner(tmp_path):
    app, agent, _ = _build(tmp_path)
    first = TestClient(app)
    second = TestClient(app)
    body = {"conversation_id": "shared-id", "message": "hello"}

    response = first.post(
        "/api/v1/agent/chat",
        json=body,
        headers={"X-Authenticated-User-ID": "forged-user"},
    )
    repeated = first.post("/api/v1/agent/chat", json=body)
    cross_owner = second.post("/api/v1/agent/chat", json=body)

    assert response.status_code == 200
    assert repeated.status_code == 200
    assert cross_owner.status_code == 404
    assert agent.calls[0][1].startswith("anon:")
    assert agent.calls[0][1] != "forged-user"
    assert agent.calls[1][1] == agent.calls[0][1]


def test_anonymous_memory_is_device_scoped(tmp_path):
    app, _, memory = _build(tmp_path)
    client = TestClient(app)
    identity = client.get("/api/v1/auth/me").json()

    async def add_memory():
        candidate = await memory.create_candidate(
            MemoryCandidateCreate(
                scope_type="user",
                owner_id=identity["owner_id"],
                space_id="middle-platform",
                domain_id=None,
                memory_type="user_context",
                subject="test",
                normalized_fact="device fact",
                summary="device fact",
                source_turn_id=None,
                confidence=0.9,
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        return await memory.approve_candidate(candidate.id)

    stored = asyncio.run(add_memory())
    own = client.get("/api/v1/memory")
    other = TestClient(app).get("/api/v1/memory")

    assert [item["id"] for item in own.json()] == [stored.id]
    assert other.json() == []


def test_personal_token_can_read_memory_but_cannot_delete_it(tmp_path):
    app, _, memory = _build(tmp_path)
    auth = app.state.user_auth_service
    now = datetime.now(UTC)

    async def arrange():
        await auth.repository.upsert_feishu_user(
            open_id="ou_codex",
            tenant_key="tenant",
            display_name="Codex User",
            avatar_url=None,
            now=now,
        )
        created = await auth.personal_tokens.create(
            "ou_codex",
            name="Codex",
            scopes={"memory:read"},
            now=now,
        )
        candidate = await memory.create_candidate(
            MemoryCandidateCreate(
                scope_type="user",
                owner_id="ou_codex",
                space_id="middle-platform",
                domain_id=None,
                memory_type="user_context",
                subject="codex",
                normalized_fact="shared fact",
                summary="shared fact",
                source_turn_id=None,
                confidence=0.9,
            )
        )
        stored = await memory.approve_candidate(candidate.id)
        return created.plaintext, stored.id

    token, memory_id = asyncio.run(arrange())
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Client-Channel": "codex",
    }
    client = TestClient(app)

    listed = client.get("/api/v1/memory", headers=headers)
    deleted = client.delete(f"/api/v1/memory/{memory_id}", headers=headers)
    chat = client.post(
        "/api/v1/agent/chat", json={"message": "hello"}, headers=headers
    )

    assert [item["id"] for item in listed.json()] == [memory_id]
    assert deleted.status_code == 403
    assert chat.status_code == 403
