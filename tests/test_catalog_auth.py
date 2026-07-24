from datetime import UTC, datetime, timedelta
import hashlib

import aiosqlite
import pytest

from knowledge.catalog import (
    AdminSessionService,
    CsrfValidationError,
    InvalidAdminSessionError,
    SharedAdminAuthenticator,
)


class RecordingVerifier:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def verify(self, password: str, password_hash: str) -> bool:
        self.calls.append((password, password_hash))
        return password == "correct" and password_hash == "stored-hash"


def test_shared_admin_authenticator_uses_injected_hash_verifier():
    verifier = RecordingVerifier()
    authenticator = SharedAdminAuthenticator(
        username="knowledge-admin",
        password_hash="stored-hash",
        verifier=verifier,
    )

    assert authenticator.authenticate("knowledge-admin", "correct") is True
    assert authenticator.authenticate("knowledge-admin", "wrong") is False
    assert authenticator.authenticate("someone-else", "correct") is False
    assert verifier.calls == [
        ("correct", "stored-hash"),
        ("wrong", "stored-hash"),
        ("correct", "stored-hash"),
    ]


def test_shared_admin_authenticator_compares_unicode_username_without_type_error():
    verifier = RecordingVerifier()
    authenticator = SharedAdminAuthenticator(
        username="知识库管理员",
        password_hash="stored-hash",
        verifier=verifier,
    )

    assert authenticator.authenticate("知识库管理员", "correct") is True
    assert authenticator.authenticate("其他管理员", "correct") is False


@pytest.mark.asyncio
async def test_admin_session_stores_only_token_hash_and_validates_csrf(tmp_path):
    from knowledge.catalog import CatalogRepository

    repository = CatalogRepository(tmp_path / "catalog.db")
    await repository.initialize()
    now = datetime(2026, 7, 15, 9, 0, tzinfo=UTC)
    service = AdminSessionService(repository, ttl=timedelta(hours=8))

    credentials = await service.create("knowledge-admin", now=now)
    session = await service.validate(
        credentials.token,
        csrf_token=credentials.csrf_token,
        now=now + timedelta(hours=1),
    )

    assert session.username == "knowledge-admin"
    assert credentials.expires_at == now + timedelta(hours=8)
    async with aiosqlite.connect(repository.db_path) as db:
        row = await (
            await db.execute(
                "SELECT token_hash, csrf_token FROM admin_sessions WHERE id=?",
                (session.id,),
            )
        ).fetchone()
    assert row is not None
    assert row[0] == hashlib.sha256(credentials.token.encode("utf-8")).hexdigest()
    assert row[1] == credentials.csrf_token

    with pytest.raises(CsrfValidationError):
        await service.validate(credentials.token, now=now + timedelta(hours=1))
    with pytest.raises(CsrfValidationError):
        await service.validate(
            credentials.token, csrf_token="错误", now=now + timedelta(hours=1)
        )
    with pytest.raises(CsrfValidationError):
        await service.logout(credentials.token, now=now + timedelta(hours=1))
    with pytest.raises(CsrfValidationError):
        await service.logout(
            credentials.token,
            csrf_token="错误",
            now=now + timedelta(hours=1),
        )

    assert await service.logout(
        credentials.token,
        csrf_token=credentials.csrf_token,
        now=now + timedelta(hours=1),
    ) is True
    with pytest.raises(InvalidAdminSessionError):
        await service.validate_read_only(
            credentials.token, now=now + timedelta(hours=1)
        )


@pytest.mark.asyncio
async def test_admin_session_rejects_expiry_and_cleanup_removes_expired_rows(tmp_path):
    from knowledge.catalog import CatalogRepository

    repository = CatalogRepository(tmp_path / "catalog.db")
    await repository.initialize()
    now = datetime(2026, 7, 15, 10, 0, tzinfo=UTC)
    service = AdminSessionService(repository, ttl=timedelta(minutes=30))
    expired = await service.create("admin", now=now)
    active = await service.create("admin", now=now + timedelta(minutes=20))

    with pytest.raises(InvalidAdminSessionError):
        await service.validate_read_only(
            expired.token, now=now + timedelta(minutes=31)
        )

    removed = await service.cleanup_expired(now=now + timedelta(minutes=31))
    assert removed == 0  # validate already removed the first expired row
    assert (
        await service.validate_read_only(
            active.token, now=now + timedelta(minutes=31)
        )
    ).username == "admin"


def test_pwdlib_password_verifier_with_real_argon2_hash_when_available():
    pwdlib = pytest.importorskip("pwdlib")
    from knowledge.catalog import PwdlibPasswordVerifier

    password_hash = pwdlib.PasswordHash.recommended()
    encoded = password_hash.hash("a-real-test-password")
    verifier = PwdlibPasswordVerifier()

    assert verifier.verify("a-real-test-password", encoded) is True
    assert verifier.verify("wrong", encoded) is False
