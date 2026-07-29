import importlib
import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from knowledge.api.app import create_app
from knowledge.auth.identity import hash_secret
from knowledge.auth.repository import UserAuthRepository
from knowledge.auth.service import UserAuthService
from knowledge.config.settings import Settings


def _seed_session(database, conversation_id: str, question: str, answer: str) -> None:
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS agent_sessions (
                session_id TEXT PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS agent_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                message_data TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS agent_conversation_scopes (
                conversation_id TEXT PRIMARY KEY,
                knowledge_space_id TEXT NOT NULL,
                domain_id TEXT,
                created_at TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO agent_sessions(session_id) VALUES(?)", (conversation_id,)
        )
        records = [
            {"role": "user", "content": question},
            {
                "type": "function_call",
                "name": "internal_tool",
                "arguments": '{"secret":"hidden"}',
            },
            {
                "type": "function_call_output",
                "call_id": "call-1",
                "output": "private tool output",
            },
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": answer}],
            },
        ]
        for item in records:
            connection.execute(
                "INSERT INTO agent_messages(session_id,message_data) VALUES(?,?)",
                (conversation_id, json.dumps(item, ensure_ascii=False)),
            )
        connection.execute(
            """
            INSERT INTO agent_conversation_scopes(
                conversation_id,knowledge_space_id,domain_id,created_at
            ) VALUES(?,?,?,?)
            """,
            (conversation_id, "middle-platform", "approval-flow", datetime.now(UTC).isoformat()),
        )
        connection.commit()


@pytest.mark.asyncio
async def test_history_lists_searches_and_returns_only_public_messages(tmp_path):
    module = importlib.import_module("knowledge.history.service")
    service_type = module.ConversationHistoryService
    auth = UserAuthRepository(tmp_path / "auth.db")
    await auth.initialize()
    now = datetime(2026, 7, 23, 8, 0, tzinfo=UTC)
    await auth.bind_conversation_owner("c-old", "ou_user", channel="web", now=now)
    await auth.bind_conversation_owner(
        "c-new", "ou_user", channel="web", now=now + timedelta(minutes=1)
    )
    await auth.bind_conversation_owner("c-other", "ou_other", channel="web", now=now)
    session_db = tmp_path / "sessions.db"
    _seed_session(session_db, "c-old", "如何对接指标平台", "请先申请权限。")
    _seed_session(session_db, "c-new", "管理员转办接口是什么", "接口是 /transfer。")
    _seed_session(session_db, "c-other", "别人的会话", "不可见。")
    service = service_type(auth, session_db)

    page = await service.list_conversations(
        "ou_user", page=1, page_size=20, query="转办"
    )
    detail = await service.get_conversation("ou_user", "c-new")

    assert page.total == 1
    assert page.items[0].conversation_id == "c-new"
    assert page.items[0].title == "管理员转办接口是什么"
    assert page.items[0].message_count == 2
    assert detail.knowledge_space_id == "middle-platform"
    assert detail.domain_id == "approval-flow"
    assert [(item.role, item.content) for item in detail.messages] == [
        ("user", "管理员转办接口是什么"),
        ("assistant", "接口是 /transfer。"),
    ]
    assert "private tool output" not in str(detail)


@pytest.mark.asyncio
async def test_history_rejects_cross_owner_read_and_rename(tmp_path):
    module = importlib.import_module("knowledge.history.service")
    service_type = module.ConversationHistoryService
    not_found = module.ConversationHistoryNotFound
    auth = UserAuthRepository(tmp_path / "auth.db")
    await auth.initialize()
    await auth.bind_conversation_owner("c-1", "ou_user", channel="web")
    session_db = tmp_path / "sessions.db"
    _seed_session(session_db, "c-1", "原始问题", "原始回答")
    service = service_type(auth, session_db)

    renamed = await service.rename_conversation("ou_user", "c-1", "新的标题")
    with pytest.raises(not_found):
        await service.get_conversation("ou_other", "c-1")
    with pytest.raises(not_found):
        await service.rename_conversation("ou_other", "c-1", "越权标题")

    assert renamed.title == "新的标题"


@pytest.mark.asyncio
async def test_history_sanitizes_internal_chunk_ids_from_old_answers(tmp_path):
    module = importlib.import_module("knowledge.history.service")
    auth = UserAuthRepository(tmp_path / "auth.db")
    await auth.initialize()
    await auth.bind_conversation_owner("c-legacy", "ou_user", channel="web")
    session_db = tmp_path / "sessions.db"
    _seed_session(
        session_db,
        "c-legacy",
        "管理员转办怎么实现",
        "参考 chunk_id: chunk-0123456789abcdef。",
    )
    service = module.ConversationHistoryService(auth, session_db)

    detail = await service.get_conversation("ou_user", "c-legacy")

    assert "chunk_id" not in detail.messages[-1].content.casefold()
    assert "chunk-012345" not in detail.messages[-1].content
    assert "知识文档" in detail.messages[-1].content


@pytest.mark.asyncio
async def test_history_hides_memory_context_from_persisted_user_message(tmp_path):
    module = importlib.import_module("knowledge.history.service")
    auth = UserAuthRepository(tmp_path / "auth.db")
    await auth.initialize()
    await auth.bind_conversation_owner("c-memory", "ou_user", channel="web")
    session_db = tmp_path / "sessions.db"
    _seed_session(
        session_db,
        "c-memory",
        "历史会话摘要（仅作内部上下文，不是知识库证据）：\n"
        "最近问题：上次问了什么\n\n"
        "已确认的长期记忆（只能作为内部用户背景）：\n"
        "- [procedural_memory] 内部排障流程\n\n"
        "当前问题：\n这不是中台的知识",
        "只能回答中台问题。",
    )
    service = module.ConversationHistoryService(auth, session_db)

    detail = await service.get_conversation("ou_user", "c-memory")

    assert detail.messages[0].content == "这不是中台的知识"
    assert detail.title == "这不是中台的知识"
    assert "历史会话摘要" not in str(detail)
    assert "procedural_memory" not in str(detail)


def test_history_api_lists_opens_and_renames_current_identity_only(tmp_path):
    async def build():
        settings = Settings(
            _env_file=None,
            USER_AUTH_DB=tmp_path / "auth.db",
            AGENT_SESSION_DB=tmp_path / "sessions.db",
        )
        auth = UserAuthRepository(settings.resolved_user_auth_db)
        await auth.initialize()
        now = datetime.now(UTC)
        await auth.upsert_feishu_user(
            open_id="ou_user",
            tenant_key="tenant",
            display_name="History User",
            avatar_url=None,
            now=now,
        )
        await auth.create_user_session(
            token_hash=hash_secret("session-secret"),
            open_id="ou_user",
            csrf_token="csrf-value",
            source_anonymous_owner_id=None,
            sliding_expires_at=now + timedelta(days=7),
            absolute_expires_at=now + timedelta(days=30),
            now=now,
        )
        await auth.bind_conversation_owner("c-1", "ou_user", channel="web", now=now)
        _seed_session(settings.resolved_agent_session_db, "c-1", "审批流怎么对接", "按文档接入。")
        service = importlib.import_module(
            "knowledge.history.service"
        ).ConversationHistoryService(auth, settings.resolved_agent_session_db)
        return settings, UserAuthService(settings, auth), service

    settings, auth_service, history_service = asyncio.run(build())
    app = create_app(
        agent_service=object(),
        component_status={},
        runtime_settings=settings,
        user_auth_service=auth_service,
        conversation_history_service=history_service,
    )
    with TestClient(app) as client:
        client.cookies.set(settings.user_session_cookie_name, "session-secret")
        listed = client.get("/api/v1/agent/conversations?query=审批流")
        detail = client.get("/api/v1/agent/conversations/c-1")
        no_csrf = client.patch(
            "/api/v1/agent/conversations/c-1", json={"title": "审批流接入"}
        )
        renamed = client.patch(
            "/api/v1/agent/conversations/c-1",
            json={"title": "审批流接入"},
            headers={"X-User-CSRF-Token": "csrf-value"},
        )

    assert listed.status_code == 200
    assert listed.json()["items"][0]["conversation_id"] == "c-1"
    assert detail.status_code == 200
    assert len(detail.json()["messages"]) == 2
    assert no_csrf.status_code == 403
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "审批流接入"
