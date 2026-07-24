import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from knowledge.auth.repository import UserAuthRepository


@pytest.mark.asyncio
async def test_user_auth_repository_initializes_secure_idempotent_schema(tmp_path):
    database = tmp_path / "user-auth.db"
    repository = UserAuthRepository(database)

    await repository.initialize()
    await repository.initialize()

    with sqlite3.connect(database) as connection:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        migration_count = connection.execute(
            "SELECT COUNT(*) FROM user_auth_schema_migrations"
        ).fetchone()[0]
        columns = {
            table: {
                row[1]
                for row in connection.execute(f"PRAGMA table_info({table})")
            }
            for table in tables
        }
        foreign_key_count = sum(
            len(connection.execute(f"PRAGMA foreign_key_list({table})").fetchall())
            for table in tables
        )

    assert journal_mode.lower() == "wal"
    assert foreign_key_count >= 5
    assert migration_count == 2
    assert "title" in columns["web_conversation_owners"]
    assert {
        "anonymous_devices",
        "feishu_users",
        "oauth_login_states",
        "user_sessions",
        "personal_api_tokens",
        "web_conversation_owners",
        "identity_merge_jobs",
        "auth_audit_events",
    } <= tables
    forbidden = {
        "token",
        "token_plaintext",
        "access_token",
        "refresh_token",
        "oauth_code",
        "raw_response",
        "phone",
        "email",
        "employee_number",
    }
    assert all(not (table_columns & forbidden) for table_columns in columns.values())


@pytest.mark.asyncio
async def test_anonymous_device_lifecycle_uses_hash_and_disables_after_merge(tmp_path):
    repository = UserAuthRepository(tmp_path / "auth.db")
    await repository.initialize()
    now = datetime(2026, 7, 23, 1, 0, tzinfo=UTC)

    created = await repository.create_anonymous_device(
        token_hash="hash-1",
        expires_at=now + timedelta(days=180),
        now=now,
    )
    loaded = await repository.get_active_anonymous_device("hash-1", now=now)
    touched = await repository.touch_anonymous_device(
        created.owner_id,
        expires_at=now + timedelta(days=181),
        now=now + timedelta(days=1),
    )
    await repository.disable_anonymous_device(
        created.owner_id, merged_to_open_id="ou_user", now=now + timedelta(days=2)
    )

    assert created.owner_id.startswith("anon:")
    assert loaded == created
    assert touched.expires_at == now + timedelta(days=181)
    assert await repository.get_active_anonymous_device(
        "hash-1", now=now + timedelta(days=2)
    ) is None


@pytest.mark.asyncio
async def test_oauth_state_is_single_use_and_feishu_user_is_upserted(tmp_path):
    repository = UserAuthRepository(tmp_path / "auth.db")
    await repository.initialize()
    now = datetime(2026, 7, 23, 2, 0, tzinfo=UTC)
    anonymous = await repository.create_anonymous_device(
        token_hash="anon-hash", expires_at=now + timedelta(days=1), now=now
    )
    await repository.upsert_feishu_user(
        open_id="ou_user",
        tenant_key="tenant",
        display_name="User One",
        avatar_url=None,
        now=now,
    )
    state = await repository.create_oauth_state(
        state_hash="state-hash",
        anonymous_owner_id=anonymous.owner_id,
        redirect_path="/chat",
        expires_at=now + timedelta(minutes=10),
        now=now,
    )

    consumed = await repository.consume_oauth_state(
        "state-hash", now=now + timedelta(minutes=1)
    )
    consumed_again = await repository.consume_oauth_state(
        "state-hash", now=now + timedelta(minutes=2)
    )
    user = await repository.get_feishu_user("ou_user")

    assert consumed.id == state.id
    assert consumed.consumed_at == now + timedelta(minutes=1)
    assert consumed_again is None
    assert user is not None and user.display_name == "User One"


@pytest.mark.asyncio
async def test_user_session_slides_without_exceeding_absolute_expiry(tmp_path):
    repository = UserAuthRepository(tmp_path / "auth.db")
    await repository.initialize()
    now = datetime(2026, 7, 23, 3, 0, tzinfo=UTC)
    await repository.upsert_feishu_user(
        open_id="ou_session",
        tenant_key="tenant",
        display_name="Session User",
        avatar_url=None,
        now=now,
    )
    created = await repository.create_user_session(
        token_hash="session-hash",
        open_id="ou_session",
        csrf_token="csrf-value",
        source_anonymous_owner_id=None,
        sliding_expires_at=now + timedelta(days=7),
        absolute_expires_at=now + timedelta(days=30),
        now=now,
    )

    active = await repository.get_active_user_session("session-hash", now=now)
    touched = await repository.touch_user_session(
        created.id,
        sliding_expires_at=now + timedelta(days=40),
        now=now + timedelta(days=6),
    )
    await repository.revoke_user_session(created.id, now=now + timedelta(days=7))

    assert active == created
    assert touched.sliding_expires_at == created.absolute_expires_at
    assert await repository.get_active_user_session(
        "session-hash", now=now + timedelta(days=7)
    ) is None


@pytest.mark.asyncio
async def test_conversation_owner_binding_rejects_cross_owner_reuse(tmp_path):
    repository = UserAuthRepository(tmp_path / "auth.db")
    await repository.initialize()
    now = datetime(2026, 7, 23, 4, 0, tzinfo=UTC)

    first = await repository.bind_conversation_owner(
        "conversation-1", "anon:first", channel="web", now=now
    )
    same = await repository.bind_conversation_owner(
        "conversation-1", "anon:first", channel="web", now=now + timedelta(minutes=1)
    )

    with pytest.raises(PermissionError):
        await repository.bind_conversation_owner(
            "conversation-1", "anon:second", channel="web", now=now
        )

    assert first.owner_id == same.owner_id == "anon:first"
    assert same.last_seen_at == now + timedelta(minutes=1)


@pytest.mark.asyncio
async def test_conversation_owners_can_be_listed_and_renamed_only_by_owner(tmp_path):
    repository = UserAuthRepository(tmp_path / "auth.db")
    await repository.initialize()
    now = datetime(2026, 7, 23, 4, 30, tzinfo=UTC)
    await repository.bind_conversation_owner(
        "conversation-old", "ou_user", channel="web", now=now
    )
    await repository.bind_conversation_owner(
        "conversation-new", "ou_user", channel="web", now=now + timedelta(minutes=1)
    )
    await repository.bind_conversation_owner(
        "conversation-other", "ou_other", channel="web", now=now
    )

    renamed = await repository.rename_conversation(
        "conversation-new", "ou_user", "管理员转办接口"
    )
    listed = await repository.list_conversations_for_owner("ou_user")

    with pytest.raises(PermissionError):
        await repository.rename_conversation(
            "conversation-new", "ou_other", "越权标题"
        )

    assert renamed.title == "管理员转办接口"
    assert [item.conversation_id for item in listed] == [
        "conversation-new",
        "conversation-old",
    ]
    assert listed[0].title == "管理员转办接口"


@pytest.mark.asyncio
async def test_merge_job_is_idempotent_and_audit_details_are_structured(tmp_path):
    repository = UserAuthRepository(tmp_path / "auth.db")
    await repository.initialize()
    now = datetime(2026, 7, 23, 5, 0, tzinfo=UTC)
    anonymous = await repository.create_anonymous_device(
        token_hash="merge-anon", expires_at=now + timedelta(days=1), now=now
    )
    await repository.upsert_feishu_user(
        open_id="ou_merge",
        tenant_key="tenant",
        display_name="Merge User",
        avatar_url=None,
        now=now,
    )

    first = await repository.get_or_create_merge_job(
        anonymous.owner_id, "ou_merge", now=now
    )
    second = await repository.get_or_create_merge_job(
        anonymous.owner_id, "ou_merge", now=now + timedelta(seconds=1)
    )
    completed = await repository.update_merge_job(
        first.id,
        status="completed",
        result={"conversations": 2},
        now=now + timedelta(seconds=2),
    )
    await repository.append_audit_event(
        actor_id="ou_merge",
        action="identity.merge",
        subject_type="merge_job",
        subject_id=first.id,
        details={"conversations": 2},
        now=now,
    )

    assert first.id == second.id
    assert completed.result == {"conversations": 2}
    assert await repository.count_audit_events() == 1


@pytest.mark.asyncio
async def test_cleanup_expired_auth_records_keeps_active_rows(tmp_path):
    repository = UserAuthRepository(tmp_path / "auth.db")
    await repository.initialize()
    now = datetime(2026, 7, 23, 6, 0, tzinfo=UTC)
    await repository.create_anonymous_device(
        token_hash="expired", expires_at=now - timedelta(seconds=1), now=now
    )
    await repository.create_anonymous_device(
        token_hash="active", expires_at=now + timedelta(days=1), now=now
    )

    counts = await repository.cleanup_expired(now=now)

    assert counts["anonymous_devices"] == 1
    assert await repository.get_active_anonymous_device("active", now=now) is not None


@pytest.mark.asyncio
async def test_auth_audit_drops_secret_fields_even_when_caller_passes_them(tmp_path):
    database = tmp_path / "auth.db"
    repository = UserAuthRepository(database)
    await repository.initialize()

    await repository.append_audit_event(
        actor_id="ou_user",
        action="auth.failure",
        subject_type="request",
        subject_id=None,
        details={
            "authorization": "Bearer personal-secret",
            "oauth_code": "one-use-code",
            "access_token": "feishu-access-token",
            "nested": {"refresh_token": "refresh-secret", "reason": "invalid"},
        },
    )

    with sqlite3.connect(database) as connection:
        serialized = connection.execute(
            "SELECT details_json FROM auth_audit_events"
        ).fetchone()[0]
    assert "personal-secret" not in serialized
    assert "one-use-code" not in serialized
    assert "feishu-access-token" not in serialized
    assert "refresh-secret" not in serialized
    assert "invalid" in serialized
