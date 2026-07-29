from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import aiosqlite

from knowledge.persistence.database import DatabaseResources
from knowledge.persistence.sqlite_compat import PostgresCompatConnection

from knowledge.auth.models import (
    AnonymousDevice,
    ConversationOwner,
    FeishuUser,
    IdentityMergeJob,
    OAuthLoginState,
    PersonalApiToken,
    UserSession,
)


_ERROR_TYPE_PATTERN = re.compile(r"[^A-Za-z0-9_.-]+")


class UserAuthRepository:
    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)

    async def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        async with self._connect() as database:
            await database.execute("PRAGMA journal_mode=WAL")
            await database.executescript(
                """
                CREATE TABLE IF NOT EXISTS user_auth_schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS anonymous_devices (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL UNIQUE,
                    token_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    disabled_at TEXT,
                    merged_to_open_id TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_anonymous_devices_expiry
                    ON anonymous_devices(expires_at, disabled_at);

                CREATE TABLE IF NOT EXISTS feishu_users (
                    open_id TEXT PRIMARY KEY,
                    tenant_key TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    avatar_url TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS oauth_login_states (
                    id TEXT PRIMARY KEY,
                    state_hash TEXT NOT NULL UNIQUE,
                    anonymous_owner_id TEXT,
                    redirect_path TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    consumed_at TEXT,
                    FOREIGN KEY(anonymous_owner_id)
                        REFERENCES anonymous_devices(owner_id)
                );
                CREATE INDEX IF NOT EXISTS idx_oauth_states_expiry
                    ON oauth_login_states(expires_at, consumed_at);

                CREATE TABLE IF NOT EXISTS user_sessions (
                    id TEXT PRIMARY KEY,
                    token_hash TEXT NOT NULL UNIQUE,
                    open_id TEXT NOT NULL,
                    csrf_token TEXT NOT NULL,
                    source_anonymous_owner_id TEXT,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    sliding_expires_at TEXT NOT NULL,
                    absolute_expires_at TEXT NOT NULL,
                    revoked_at TEXT,
                    FOREIGN KEY(open_id) REFERENCES feishu_users(open_id),
                    FOREIGN KEY(source_anonymous_owner_id)
                        REFERENCES anonymous_devices(owner_id)
                );
                CREATE INDEX IF NOT EXISTS idx_user_sessions_expiry
                    ON user_sessions(sliding_expires_at, absolute_expires_at, revoked_at);

                CREATE TABLE IF NOT EXISTS personal_api_tokens (
                    id TEXT PRIMARY KEY,
                    open_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    display_prefix TEXT NOT NULL,
                    scopes_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_used_at TEXT,
                    revoked_at TEXT,
                    FOREIGN KEY(open_id) REFERENCES feishu_users(open_id),
                    UNIQUE(open_id, name)
                );

                CREATE TABLE IF NOT EXISTS web_conversation_owners (
                    conversation_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    title TEXT,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_conversation_owner
                    ON web_conversation_owners(owner_id, last_seen_at);

                CREATE TABLE IF NOT EXISTS identity_merge_jobs (
                    id TEXT PRIMARY KEY,
                    source_anonymous_owner_id TEXT NOT NULL,
                    target_open_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending','running','completed','failed')),
                    result_json TEXT NOT NULL DEFAULT '{}',
                    error_type TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(source_anonymous_owner_id)
                        REFERENCES anonymous_devices(owner_id),
                    FOREIGN KEY(target_open_id) REFERENCES feishu_users(open_id),
                    UNIQUE(source_anonymous_owner_id, target_open_id)
                );

                CREATE TABLE IF NOT EXISTS auth_audit_events (
                    id TEXT PRIMARY KEY,
                    actor_id TEXT,
                    action TEXT NOT NULL,
                    subject_type TEXT NOT NULL,
                    subject_id TEXT,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_auth_audit_created
                    ON auth_audit_events(created_at);
                """
            )
            await database.execute(
                """
                INSERT OR IGNORE INTO user_auth_schema_migrations(version, applied_at)
                VALUES(1, ?)
                """,
                (datetime.now(UTC).isoformat(),),
            )
            owner_columns = {
                row[1]
                for row in await (
                    await database.execute("PRAGMA table_info(web_conversation_owners)")
                ).fetchall()
            }
            if "title" not in owner_columns:
                await database.execute(
                    "ALTER TABLE web_conversation_owners ADD COLUMN title TEXT"
                )
            await database.execute(
                """
                INSERT OR IGNORE INTO user_auth_schema_migrations(version, applied_at)
                VALUES(2, ?)
                """,
                (datetime.now(UTC).isoformat(),),
            )
            await database.commit()

    async def create_anonymous_device(
        self, *, token_hash: str, expires_at: datetime, now: datetime | None = None
    ) -> AnonymousDevice:
        current = _utc(now)
        device_id = str(uuid4())
        owner_id = f"anon:{device_id}"
        async with self._connect() as database:
            await database.execute(
                """
                INSERT INTO anonymous_devices(
                    id,owner_id,token_hash,created_at,last_seen_at,expires_at,
                    disabled_at,merged_to_open_id
                ) VALUES(?,?,?,?,?,?,NULL,NULL)
                """,
                (
                    device_id,
                    owner_id,
                    _required(token_hash, "token_hash"),
                    _iso(current),
                    _iso(current),
                    _iso(expires_at),
                ),
            )
            await database.commit()
        return await self.get_anonymous_device_by_owner(owner_id)

    async def get_anonymous_device_by_owner(
        self, owner_id: str
    ) -> AnonymousDevice:
        async with self._connect() as database:
            row = await (
                await database.execute(
                    "SELECT * FROM anonymous_devices WHERE owner_id=?", (owner_id,)
                )
            ).fetchone()
        if row is None:
            raise KeyError(owner_id)
        return _anonymous_device(row)

    async def get_active_anonymous_device(
        self, token_hash: str, *, now: datetime | None = None
    ) -> AnonymousDevice | None:
        current = _utc(now)
        async with self._connect() as database:
            row = await (
                await database.execute(
                    """
                    SELECT * FROM anonymous_devices
                    WHERE token_hash=? AND disabled_at IS NULL AND expires_at>?
                    """,
                    (token_hash, _iso(current)),
                )
            ).fetchone()
        return _anonymous_device(row) if row else None

    async def touch_anonymous_device(
        self,
        owner_id: str,
        *,
        expires_at: datetime,
        now: datetime | None = None,
    ) -> AnonymousDevice:
        current = _utc(now)
        async with self._connect() as database:
            cursor = await database.execute(
                """
                UPDATE anonymous_devices
                SET last_seen_at=?, expires_at=?
                WHERE owner_id=? AND disabled_at IS NULL
                """,
                (_iso(current), _iso(expires_at), owner_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(owner_id)
            await database.commit()
        return await self.get_anonymous_device_by_owner(owner_id)

    async def disable_anonymous_device(
        self,
        owner_id: str,
        *,
        merged_to_open_id: str | None = None,
        now: datetime | None = None,
    ) -> None:
        async with self._connect() as database:
            await database.execute(
                """
                UPDATE anonymous_devices
                SET disabled_at=?, merged_to_open_id=?
                WHERE owner_id=? AND disabled_at IS NULL
                """,
                (_iso(_utc(now)), merged_to_open_id, owner_id),
            )
            await database.commit()

    async def upsert_feishu_user(
        self,
        *,
        open_id: str,
        tenant_key: str,
        display_name: str,
        avatar_url: str | None,
        now: datetime | None = None,
    ) -> FeishuUser:
        current = _utc(now)
        async with self._connect() as database:
            await database.execute(
                """
                INSERT INTO feishu_users(
                    open_id,tenant_key,display_name,avatar_url,created_at,updated_at
                ) VALUES(?,?,?,?,?,?)
                ON CONFLICT(open_id) DO UPDATE SET
                    tenant_key=excluded.tenant_key,
                    display_name=excluded.display_name,
                    avatar_url=excluded.avatar_url,
                    updated_at=excluded.updated_at
                """,
                (
                    _required(open_id, "open_id"),
                    _required(tenant_key, "tenant_key"),
                    _required(display_name, "display_name"),
                    avatar_url,
                    _iso(current),
                    _iso(current),
                ),
            )
            await database.commit()
        user = await self.get_feishu_user(open_id)
        assert user is not None
        return user

    async def get_feishu_user(self, open_id: str) -> FeishuUser | None:
        async with self._connect() as database:
            row = await (
                await database.execute(
                    "SELECT * FROM feishu_users WHERE open_id=?", (open_id,)
                )
            ).fetchone()
        return _feishu_user(row) if row else None

    async def create_oauth_state(
        self,
        *,
        state_hash: str,
        anonymous_owner_id: str | None,
        redirect_path: str,
        expires_at: datetime,
        now: datetime | None = None,
    ) -> OAuthLoginState:
        state_id = str(uuid4())
        current = _utc(now)
        async with self._connect() as database:
            await database.execute(
                """
                INSERT INTO oauth_login_states(
                    id,state_hash,anonymous_owner_id,redirect_path,expires_at,
                    created_at,consumed_at
                ) VALUES(?,?,?,?,?,?,NULL)
                """,
                (
                    state_id,
                    _required(state_hash, "state_hash"),
                    anonymous_owner_id,
                    redirect_path if redirect_path.startswith("/") else "/chat",
                    _iso(expires_at),
                    _iso(current),
                ),
            )
            await database.commit()
        return await self._get_oauth_state(state_id)

    async def consume_oauth_state(
        self, state_hash: str, *, now: datetime | None = None
    ) -> OAuthLoginState | None:
        current = _utc(now)
        async with self._connect() as database:
            await database.execute("BEGIN IMMEDIATE")
            row = await (
                await database.execute(
                    """
                    SELECT * FROM oauth_login_states
                    WHERE state_hash=? AND consumed_at IS NULL AND expires_at>?
                    """,
                    (state_hash, _iso(current)),
                )
            ).fetchone()
            if row is None:
                await database.rollback()
                return None
            await database.execute(
                "UPDATE oauth_login_states SET consumed_at=? WHERE id=?",
                (_iso(current), row["id"]),
            )
            await database.commit()
        return await self._get_oauth_state(row["id"])

    async def _get_oauth_state(self, state_id: str) -> OAuthLoginState:
        async with self._connect() as database:
            row = await (
                await database.execute(
                    "SELECT * FROM oauth_login_states WHERE id=?", (state_id,)
                )
            ).fetchone()
        if row is None:
            raise KeyError(state_id)
        return _oauth_state(row)

    async def create_user_session(
        self,
        *,
        token_hash: str,
        open_id: str,
        csrf_token: str,
        source_anonymous_owner_id: str | None,
        sliding_expires_at: datetime,
        absolute_expires_at: datetime,
        now: datetime | None = None,
    ) -> UserSession:
        session_id = str(uuid4())
        current = _utc(now)
        if sliding_expires_at > absolute_expires_at:
            raise ValueError("sliding expiry cannot exceed absolute expiry")
        async with self._connect() as database:
            await database.execute(
                """
                INSERT INTO user_sessions(
                    id,token_hash,open_id,csrf_token,source_anonymous_owner_id,
                    created_at,last_seen_at,sliding_expires_at,
                    absolute_expires_at,revoked_at
                ) VALUES(?,?,?,?,?,?,?,?,?,NULL)
                """,
                (
                    session_id,
                    _required(token_hash, "token_hash"),
                    _required(open_id, "open_id"),
                    _required(csrf_token, "csrf_token"),
                    source_anonymous_owner_id,
                    _iso(current),
                    _iso(current),
                    _iso(sliding_expires_at),
                    _iso(absolute_expires_at),
                ),
            )
            await database.commit()
        return await self.get_user_session(session_id)

    async def get_user_session(self, session_id: str) -> UserSession:
        async with self._connect() as database:
            row = await (
                await database.execute(
                    "SELECT * FROM user_sessions WHERE id=?", (session_id,)
                )
            ).fetchone()
        if row is None:
            raise KeyError(session_id)
        return _user_session(row)

    async def get_active_user_session(
        self, token_hash: str, *, now: datetime | None = None
    ) -> UserSession | None:
        current = _iso(_utc(now))
        async with self._connect() as database:
            row = await (
                await database.execute(
                    """
                    SELECT * FROM user_sessions
                    WHERE token_hash=? AND revoked_at IS NULL
                      AND sliding_expires_at>? AND absolute_expires_at>?
                    """,
                    (token_hash, current, current),
                )
            ).fetchone()
        return _user_session(row) if row else None

    async def touch_user_session(
        self,
        session_id: str,
        *,
        sliding_expires_at: datetime,
        now: datetime | None = None,
    ) -> UserSession:
        session = await self.get_user_session(session_id)
        bounded = min(sliding_expires_at, session.absolute_expires_at)
        async with self._connect() as database:
            await database.execute(
                """
                UPDATE user_sessions SET last_seen_at=?, sliding_expires_at=?
                WHERE id=? AND revoked_at IS NULL
                """,
                (_iso(_utc(now)), _iso(bounded), session_id),
            )
            await database.commit()
        return await self.get_user_session(session_id)

    async def revoke_user_session(
        self, session_id: str, *, now: datetime | None = None
    ) -> None:
        async with self._connect() as database:
            await database.execute(
                "UPDATE user_sessions SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
                (_iso(_utc(now)), session_id),
            )
            await database.commit()

    async def clear_user_session_merge_source(self, session_id: str) -> None:
        async with self._connect() as database:
            await database.execute(
                """
                UPDATE user_sessions SET source_anonymous_owner_id=NULL
                WHERE id=?
                """,
                (session_id,),
            )
            await database.commit()

    async def create_personal_api_token(
        self,
        *,
        open_id: str,
        name: str,
        token_hash: str,
        display_prefix: str,
        scopes: frozenset[str],
        now: datetime | None = None,
    ) -> PersonalApiToken:
        token_id = str(uuid4())
        try:
            async with self._connect() as database:
                await database.execute(
                    """
                    INSERT INTO personal_api_tokens(
                        id,open_id,name,token_hash,display_prefix,scopes_json,
                        created_at,last_used_at,revoked_at
                    ) VALUES(?,?,?,?,?,?,?,NULL,NULL)
                    """,
                    (
                        token_id,
                        _required(open_id, "open_id"),
                        _required(name, "name"),
                        _required(token_hash, "token_hash"),
                        _required(display_prefix, "display_prefix"),
                        json.dumps(sorted(scopes)),
                        _iso(_utc(now)),
                    ),
                )
                await database.commit()
        except aiosqlite.IntegrityError as exc:
            raise ValueError("token name must be unique") from exc
        return await self.get_personal_api_token(token_id)

    async def get_personal_api_token(self, token_id: str) -> PersonalApiToken:
        async with self._connect() as database:
            row = await (
                await database.execute(
                    "SELECT * FROM personal_api_tokens WHERE id=?", (token_id,)
                )
            ).fetchone()
        if row is None:
            raise KeyError(token_id)
        return _personal_token(row)

    async def get_active_personal_api_token(
        self, token_hash: str
    ) -> PersonalApiToken | None:
        async with self._connect() as database:
            row = await (
                await database.execute(
                    """
                    SELECT * FROM personal_api_tokens
                    WHERE token_hash=? AND revoked_at IS NULL
                    """,
                    (token_hash,),
                )
            ).fetchone()
        return _personal_token(row) if row else None

    async def list_personal_api_tokens(
        self, open_id: str
    ) -> list[PersonalApiToken]:
        async with self._connect() as database:
            rows = await (
                await database.execute(
                    """
                    SELECT * FROM personal_api_tokens
                    WHERE open_id=? ORDER BY created_at DESC
                    """,
                    (open_id,),
                )
            ).fetchall()
        return [_personal_token(row) for row in rows]

    async def touch_personal_api_token(
        self, token_id: str, *, now: datetime | None = None
    ) -> PersonalApiToken:
        async with self._connect() as database:
            await database.execute(
                """
                UPDATE personal_api_tokens SET last_used_at=?
                WHERE id=? AND revoked_at IS NULL
                """,
                (_iso(_utc(now)), token_id),
            )
            await database.commit()
        return await self.get_personal_api_token(token_id)

    async def revoke_personal_api_token(
        self,
        open_id: str,
        token_id: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        async with self._connect() as database:
            cursor = await database.execute(
                """
                UPDATE personal_api_tokens SET revoked_at=?
                WHERE id=? AND open_id=? AND revoked_at IS NULL
                """,
                (_iso(_utc(now)), token_id, open_id),
            )
            await database.commit()
            return cursor.rowcount == 1

    async def bind_conversation_owner(
        self,
        conversation_id: str,
        owner_id: str,
        *,
        channel: str,
        now: datetime | None = None,
    ) -> ConversationOwner:
        conversation_id = _required(conversation_id, "conversation_id")
        owner_id = _required(owner_id, "owner_id")
        current = _utc(now)
        async with self._connect() as database:
            await database.execute("BEGIN IMMEDIATE")
            row = await (
                await database.execute(
                    """
                    SELECT * FROM web_conversation_owners WHERE conversation_id=?
                    """,
                    (conversation_id,),
                )
            ).fetchone()
            if row is not None and row["owner_id"] != owner_id:
                await database.rollback()
                raise PermissionError("conversation is owned by another identity")
            if row is None:
                await database.execute(
                    """
                    INSERT INTO web_conversation_owners(
                        conversation_id,owner_id,channel,created_at,last_seen_at
                    ) VALUES(?,?,?,?,?)
                    """,
                    (conversation_id, owner_id, channel, _iso(current), _iso(current)),
                )
            else:
                await database.execute(
                    """
                    UPDATE web_conversation_owners SET channel=?,last_seen_at=?
                    WHERE conversation_id=?
                    """,
                    (channel, _iso(current), conversation_id),
                )
            await database.commit()
        return await self.get_conversation_owner(conversation_id)

    async def get_conversation_owner(
        self, conversation_id: str
    ) -> ConversationOwner:
        async with self._connect() as database:
            row = await (
                await database.execute(
                    """
                    SELECT * FROM web_conversation_owners WHERE conversation_id=?
                    """,
                    (conversation_id,),
                )
            ).fetchone()
        if row is None:
            raise KeyError(conversation_id)
        return _conversation_owner(row)

    async def delete_conversation_owner(
        self, conversation_id: str, owner_id: str
    ) -> bool:
        async with self._connect() as database:
            await database.execute("BEGIN IMMEDIATE")
            row = await (
                await database.execute(
                    """
                    SELECT owner_id FROM web_conversation_owners
                    WHERE conversation_id=?
                    """,
                    (conversation_id,),
                )
            ).fetchone()
            if row is None:
                await database.rollback()
                return False
            if row["owner_id"] != owner_id:
                await database.rollback()
                raise PermissionError("conversation is owned by another identity")
            await database.execute(
                "DELETE FROM web_conversation_owners WHERE conversation_id=?",
                (conversation_id,),
            )
            await database.commit()
            return True

    async def count_conversations_for_owner(self, owner_id: str) -> int:
        async with self._connect() as database:
            row = await (
                await database.execute(
                    """
                    SELECT COUNT(*) AS count FROM web_conversation_owners
                    WHERE owner_id=?
                    """,
                    (owner_id,),
                )
            ).fetchone()
        return int(row["count"])

    async def list_conversations_for_owner(
        self, owner_id: str
    ) -> list[ConversationOwner]:
        async with self._connect() as database:
            rows = await (
                await database.execute(
                    """
                    SELECT * FROM web_conversation_owners
                    WHERE owner_id=?
                    ORDER BY last_seen_at DESC, conversation_id ASC
                    """,
                    (_required(owner_id, "owner_id"),),
                )
            ).fetchall()
        return [_conversation_owner(row) for row in rows]

    async def rename_conversation(
        self, conversation_id: str, owner_id: str, title: str
    ) -> ConversationOwner:
        normalized_title = " ".join(_required(title, "title").split())
        if len(normalized_title) > 100:
            raise ValueError("conversation title is too long")
        async with self._connect() as database:
            cursor = await database.execute(
                """
                UPDATE web_conversation_owners SET title=?
                WHERE conversation_id=? AND owner_id=?
                """,
                (
                    normalized_title,
                    _required(conversation_id, "conversation_id"),
                    _required(owner_id, "owner_id"),
                ),
            )
            await database.commit()
            if cursor.rowcount != 1:
                raise PermissionError("conversation is not owned by this identity")
        return await self.get_conversation_owner(conversation_id)

    async def transfer_conversation_owners(
        self,
        source_owner_id: str,
        target_owner_id: str,
        *,
        now: datetime | None = None,
    ) -> int:
        async with self._connect() as database:
            cursor = await database.execute(
                """
                UPDATE web_conversation_owners SET owner_id=?,last_seen_at=?
                WHERE owner_id=?
                """,
                (target_owner_id, _iso(_utc(now)), source_owner_id),
            )
            await database.commit()
            return cursor.rowcount

    async def get_or_create_merge_job(
        self,
        source_anonymous_owner_id: str,
        target_open_id: str,
        *,
        now: datetime | None = None,
    ) -> IdentityMergeJob:
        current = _utc(now)
        job_id = str(uuid4())
        async with self._connect() as database:
            await database.execute(
                """
                INSERT OR IGNORE INTO identity_merge_jobs(
                    id,source_anonymous_owner_id,target_open_id,status,
                    result_json,error_type,created_at,updated_at
                ) VALUES(?,?,?,'pending','{}',NULL,?,?)
                """,
                (
                    job_id,
                    source_anonymous_owner_id,
                    target_open_id,
                    _iso(current),
                    _iso(current),
                ),
            )
            await database.commit()
            row = await (
                await database.execute(
                    """
                    SELECT * FROM identity_merge_jobs
                    WHERE source_anonymous_owner_id=? AND target_open_id=?
                    """,
                    (source_anonymous_owner_id, target_open_id),
                )
            ).fetchone()
        return _merge_job(row)

    async def update_merge_job(
        self,
        job_id: str,
        *,
        status: str,
        result: dict[str, int] | None = None,
        error_type: str | None = None,
        now: datetime | None = None,
    ) -> IdentityMergeJob:
        sanitized_error = (
            _ERROR_TYPE_PATTERN.sub("", str(error_type))[:100] or None
            if error_type
            else None
        )
        async with self._connect() as database:
            await database.execute(
                """
                UPDATE identity_merge_jobs
                SET status=?,result_json=?,error_type=?,updated_at=? WHERE id=?
                """,
                (
                    status,
                    json.dumps(result or {}, sort_keys=True),
                    sanitized_error,
                    _iso(_utc(now)),
                    job_id,
                ),
            )
            await database.commit()
            row = await (
                await database.execute(
                    "SELECT * FROM identity_merge_jobs WHERE id=?", (job_id,)
                )
            ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return _merge_job(row)

    async def get_merge_job(self, job_id: str) -> IdentityMergeJob:
        async with self._connect() as database:
            row = await (
                await database.execute(
                    "SELECT * FROM identity_merge_jobs WHERE id=?", (job_id,)
                )
            ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return _merge_job(row)

    async def append_audit_event(
        self,
        *,
        actor_id: str | None,
        action: str,
        subject_type: str,
        subject_id: str | None,
        details: dict[str, object] | None = None,
        now: datetime | None = None,
    ) -> None:
        safe_details = _sanitize_audit_value(details or {})
        async with self._connect() as database:
            await database.execute(
                """
                INSERT INTO auth_audit_events(
                    id,actor_id,action,subject_type,subject_id,details_json,created_at
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    str(uuid4()),
                    actor_id,
                    _required(action, "action"),
                    _required(subject_type, "subject_type"),
                    subject_id,
                    json.dumps(safe_details, ensure_ascii=True, sort_keys=True),
                    _iso(_utc(now)),
                ),
            )
            await database.commit()

    async def count_audit_events(self) -> int:
        async with self._connect() as database:
            row = await (
                await database.execute("SELECT COUNT(*) AS count FROM auth_audit_events")
            ).fetchone()
        return int(row["count"])

    async def cleanup_expired(
        self, *, now: datetime | None = None
    ) -> dict[str, int]:
        current = _iso(_utc(now))
        async with self._connect() as database:
            oauth_cursor = await database.execute(
                "DELETE FROM oauth_login_states WHERE expires_at<=?", (current,)
            )
            session_cursor = await database.execute(
                """
                DELETE FROM user_sessions
                WHERE absolute_expires_at<=? OR revoked_at IS NOT NULL
                """,
                (current,),
            )
            anonymous_cursor = await database.execute(
                """
                DELETE FROM anonymous_devices
                WHERE expires_at<=? AND NOT EXISTS(
                    SELECT 1 FROM oauth_login_states s
                    WHERE s.anonymous_owner_id=anonymous_devices.owner_id
                ) AND NOT EXISTS(
                    SELECT 1 FROM user_sessions u
                    WHERE u.source_anonymous_owner_id=anonymous_devices.owner_id
                ) AND NOT EXISTS(
                    SELECT 1 FROM identity_merge_jobs j
                    WHERE j.source_anonymous_owner_id=anonymous_devices.owner_id
                )
                """,
                (current,),
            )
            await database.commit()
        return {
            "oauth_login_states": oauth_cursor.rowcount,
            "user_sessions": session_cursor.rowcount,
            "anonymous_devices": anonymous_cursor.rowcount,
        }

    def _connect(self) -> _Connection:
        return _Connection(self.database_path)


class _Connection:
    def __init__(self, path: Path):
        self.path = path
        self.connection: aiosqlite.Connection | None = None

    async def __aenter__(self) -> aiosqlite.Connection:
        self.connection = await aiosqlite.connect(self.path, timeout=5)
        self.connection.row_factory = aiosqlite.Row
        await self.connection.execute("PRAGMA busy_timeout=5000")
        await self.connection.execute("PRAGMA foreign_keys=ON")
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        if self.connection is None:
            return
        if exc_type is not None:
            await self.connection.rollback()
        await self.connection.close()


class PostgresUserAuthRepository(UserAuthRepository):
    def __init__(self, database_resources: DatabaseResources):
        self.database_resources = database_resources

    async def initialize(self) -> None:
        if not await self.database_resources.check_ready():
            raise RuntimeError("PostgreSQL user-auth repository is unavailable")

    def _connect(self) -> PostgresCompatConnection:
        return PostgresCompatConnection(self.database_resources)


def _required(value: str, name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} is required")
    return normalized


def _sanitize_audit_value(value):
    if isinstance(value, dict):
        output = {}
        for key, item in value.items():
            normalized = str(key).casefold().replace("-", "_")
            if any(
                marker in normalized
                for marker in (
                    "authorization",
                    "token",
                    "secret",
                    "password",
                    "oauth_code",
                    "cookie",
                )
            ):
                continue
            output[str(key)[:100]] = _sanitize_audit_value(item)
        return output
    if isinstance(value, (list, tuple)):
        return [_sanitize_audit_value(item) for item in value[:100]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value[:500] if isinstance(value, str) else value
    return type(value).__name__


def _utc(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return current.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat()


def _datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _anonymous_device(row) -> AnonymousDevice:
    return AnonymousDevice(
        id=row["id"],
        owner_id=row["owner_id"],
        token_hash=row["token_hash"],
        created_at=_datetime(row["created_at"]),
        last_seen_at=_datetime(row["last_seen_at"]),
        expires_at=_datetime(row["expires_at"]),
        disabled_at=_datetime(row["disabled_at"]),
        merged_to_open_id=row["merged_to_open_id"],
    )


def _feishu_user(row) -> FeishuUser:
    return FeishuUser(
        open_id=row["open_id"],
        tenant_key=row["tenant_key"],
        display_name=row["display_name"],
        avatar_url=row["avatar_url"],
        created_at=_datetime(row["created_at"]),
        updated_at=_datetime(row["updated_at"]),
    )


def _oauth_state(row) -> OAuthLoginState:
    return OAuthLoginState(
        id=row["id"],
        state_hash=row["state_hash"],
        anonymous_owner_id=row["anonymous_owner_id"],
        redirect_path=row["redirect_path"],
        expires_at=_datetime(row["expires_at"]),
        created_at=_datetime(row["created_at"]),
        consumed_at=_datetime(row["consumed_at"]),
    )


def _user_session(row) -> UserSession:
    return UserSession(
        id=row["id"],
        token_hash=row["token_hash"],
        open_id=row["open_id"],
        csrf_token=row["csrf_token"],
        source_anonymous_owner_id=row["source_anonymous_owner_id"],
        created_at=_datetime(row["created_at"]),
        last_seen_at=_datetime(row["last_seen_at"]),
        sliding_expires_at=_datetime(row["sliding_expires_at"]),
        absolute_expires_at=_datetime(row["absolute_expires_at"]),
        revoked_at=_datetime(row["revoked_at"]),
    )


def _conversation_owner(row) -> ConversationOwner:
    return ConversationOwner(
        conversation_id=row["conversation_id"],
        owner_id=row["owner_id"],
        channel=row["channel"],
        title=row["title"],
        created_at=_datetime(row["created_at"]),
        last_seen_at=_datetime(row["last_seen_at"]),
    )


def _merge_job(row) -> IdentityMergeJob:
    return IdentityMergeJob(
        id=row["id"],
        source_anonymous_owner_id=row["source_anonymous_owner_id"],
        target_open_id=row["target_open_id"],
        status=row["status"],
        result=json.loads(row["result_json"] or "{}"),
        error_type=row["error_type"],
        created_at=_datetime(row["created_at"]),
        updated_at=_datetime(row["updated_at"]),
    )


def _personal_token(row) -> PersonalApiToken:
    return PersonalApiToken(
        id=row["id"],
        open_id=row["open_id"],
        name=row["name"],
        token_hash=row["token_hash"],
        display_prefix=row["display_prefix"],
        scopes=frozenset(json.loads(row["scopes_json"] or "[]")),
        created_at=_datetime(row["created_at"]),
        last_used_at=_datetime(row["last_used_at"]),
        revoked_at=_datetime(row["revoked_at"]),
    )
