from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import uuid4

from knowledge.catalog.models import (
    AdminSession,
    AdminSessionCredentials,
)
from knowledge.catalog.repository import CatalogRepository


class PasswordVerifier(Protocol):
    def verify(self, password: str, password_hash: str) -> bool: ...


class PwdlibPasswordVerifier:
    """Uses pwdlib when the optional runtime dependency is installed."""

    def __init__(self) -> None:
        try:
            from pwdlib import PasswordHash
        except ImportError as exc:
            raise RuntimeError(
                "pwdlib is required unless a PasswordVerifier is injected"
            ) from exc
        self._password_hash = PasswordHash.recommended()

    def verify(self, password: str, password_hash: str) -> bool:
        try:
            return bool(self._password_hash.verify(password, password_hash))
        except Exception:
            return False


class SharedAdminAuthenticator:
    def __init__(
        self,
        *,
        username: str,
        password_hash: str,
        verifier: PasswordVerifier | None = None,
    ) -> None:
        if not username or not password_hash:
            raise ValueError("admin username and password hash are required")
        self._username = username
        self._password_hash = password_hash
        self._verifier = verifier or PwdlibPasswordVerifier()

    def authenticate(self, username: str, password: str) -> bool:
        username_matches = _safe_text_compare(username, self._username)
        password_matches = self._verifier.verify(password, self._password_hash)
        return username_matches and password_matches


class InvalidAdminSessionError(LookupError):
    pass


class CsrfValidationError(PermissionError):
    pass


class AdminSessionService:
    def __init__(
        self,
        repository: CatalogRepository,
        *,
        ttl: timedelta = timedelta(hours=8),
    ) -> None:
        if ttl <= timedelta(0):
            raise ValueError("admin session ttl must be positive")
        self._repository = repository
        self._ttl = ttl

    async def create(
        self, username: str, *, now: datetime | None = None
    ) -> AdminSessionCredentials:
        now = self._normalize_time(now)
        token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        expires_at = now + self._ttl
        await self._repository._insert_admin_session(
            session_id=str(uuid4()),
            token_hash=self._token_hash(token),
            username=username,
            csrf_token=csrf_token,
            expires_at=expires_at,
            created_at=now,
        )
        return AdminSessionCredentials(
            token=token,
            csrf_token=csrf_token,
            expires_at=expires_at,
        )

    async def validate(
        self,
        token: str,
        *,
        csrf_token: str | None = None,
        now: datetime | None = None,
    ) -> AdminSession:
        return await self._validate(
            token, csrf_token=csrf_token, require_csrf=True, now=now
        )

    async def validate_read_only(
        self, token: str, *, now: datetime | None = None
    ) -> AdminSession:
        return await self._validate(
            token, csrf_token=None, require_csrf=False, now=now
        )

    async def get_csrf_token(
        self, token: str, *, now: datetime | None = None
    ) -> str:
        await self.validate_read_only(token, now=now)
        stored = await self._repository._get_admin_session(self._token_hash(token))
        if stored is None:
            raise InvalidAdminSessionError("admin session is invalid")
        return stored.csrf_token

    async def _validate(
        self,
        token: str,
        *,
        csrf_token: str | None,
        require_csrf: bool,
        now: datetime | None,
    ) -> AdminSession:
        now = self._normalize_time(now)
        token_hash = self._token_hash(token)
        stored = await self._repository._get_admin_session(token_hash)
        if stored is None:
            raise InvalidAdminSessionError("admin session is invalid")
        if stored.expires_at <= now:
            await self._repository._delete_admin_session(token_hash)
            raise InvalidAdminSessionError("admin session has expired")
        if require_csrf and (
            csrf_token is None
            or not _safe_text_compare(csrf_token, stored.csrf_token)
        ):
            raise CsrfValidationError("CSRF token is invalid")
        return AdminSession(
            id=stored.id,
            username=stored.username,
            expires_at=stored.expires_at,
            created_at=stored.created_at,
        )

    async def logout(
        self,
        token: str,
        *,
        csrf_token: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        await self.validate(token, csrf_token=csrf_token, now=now)
        return await self._repository._delete_admin_session(
            self._token_hash(token)
        )

    async def cleanup_expired(self, *, now: datetime | None = None) -> int:
        return await self._repository._cleanup_admin_sessions(
            self._normalize_time(now)
        )

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _normalize_time(value: datetime | None) -> datetime:
        value = value or datetime.now(UTC)
        if value.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        return value.astimezone(UTC)


def _safe_text_compare(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))
