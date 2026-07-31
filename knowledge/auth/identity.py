from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable, Protocol

from knowledge.auth.models import ResolvedIdentity
from knowledge.auth.repository import UserAuthRepository


BROWSER_SCOPES = frozenset(
    {
        "agent:query",
        "agent:approve",
        "memory:read",
        "memory:delete",
        "account:manage",
    }
)


@dataclass(frozen=True, slots=True)
class IdentityResolution:
    identity: ResolvedIdentity
    cookie_value: str | None = None
    clear_user_cookie: bool = False


class AuthenticationError(RuntimeError):
    pass


class AuthorizationError(RuntimeError):
    pass


class PersonalTokenAuthenticator(Protocol):
    async def authenticate(
        self, token: str, *, now: datetime | None = None
    ) -> ResolvedIdentity | None: ...


class AnonymousIdentityService:
    def __init__(
        self,
        repository: UserAuthRepository,
        *,
        ttl_seconds: int,
        token_factory: Callable[[], str] | None = None,
    ):
        self.repository = repository
        self.ttl_seconds = ttl_seconds
        self.token_factory = token_factory or (lambda: secrets.token_urlsafe(32))

    async def resolve(
        self, cookie_value: str | None, *, now: datetime | None = None
    ) -> IdentityResolution:
        current = _utc(now)
        if cookie_value:
            device = await self.repository.refresh_active_anonymous_device(
                hash_secret(cookie_value),
                expires_at=current + timedelta(seconds=self.ttl_seconds),
                now=current,
            )
            if device is not None:
                return IdentityResolution(
                    identity=ResolvedIdentity(
                        owner_id=device.owner_id,
                        kind="anonymous",
                        display_name="当前设备",
                        scopes=BROWSER_SCOPES,
                    ),
                    cookie_value=cookie_value,
                )

        secret = self.token_factory()
        device = await self.repository.create_anonymous_device(
            token_hash=hash_secret(secret),
            expires_at=current + timedelta(seconds=self.ttl_seconds),
            now=current,
        )
        return IdentityResolution(
            identity=ResolvedIdentity(
                owner_id=device.owner_id,
                kind="anonymous",
                display_name="当前设备",
                scopes=BROWSER_SCOPES,
            ),
            cookie_value=secret,
        )


class RequestIdentityResolver:
    def __init__(
        self,
        repository: UserAuthRepository,
        anonymous_service: AnonymousIdentityService,
        *,
        session_sliding_ttl_seconds: int,
        token_authenticator: PersonalTokenAuthenticator | None = None,
    ):
        self.repository = repository
        self.anonymous_service = anonymous_service
        self.session_sliding_ttl_seconds = session_sliding_ttl_seconds
        self.token_authenticator = token_authenticator

    async def resolve(
        self,
        *,
        authorization: str | None,
        user_session_cookie: str | None,
        anonymous_cookie: str | None,
        now: datetime | None = None,
    ) -> IdentityResolution:
        current = _utc(now)
        if authorization:
            scheme, separator, token = authorization.partition(" ")
            if (
                scheme.casefold() != "bearer"
                or not separator
                or not token.strip()
                or self.token_authenticator is None
            ):
                raise AuthenticationError("invalid bearer credentials")
            identity = await self.token_authenticator.authenticate(
                token.strip(), now=current
            )
            if identity is None:
                raise AuthenticationError("invalid bearer credentials")
            return IdentityResolution(identity=identity)

        clear_user_cookie = False
        if user_session_cookie:
            session = await self.repository.get_active_user_session(
                hash_secret(user_session_cookie), now=current
            )
            if session is not None:
                user = await self.repository.get_feishu_user(session.open_id)
                if user is not None:
                    session = await self.repository.touch_user_session(
                        session.id,
                        sliding_expires_at=current
                        + timedelta(seconds=self.session_sliding_ttl_seconds),
                        now=current,
                    )
                    return IdentityResolution(
                        identity=ResolvedIdentity(
                            owner_id=user.open_id,
                            kind="feishu",
                            display_name=user.display_name,
                            scopes=BROWSER_SCOPES,
                            session_id=session.id,
                            csrf_token=session.csrf_token,
                        )
                    )
            clear_user_cookie = True

        anonymous = await self.anonymous_service.resolve(
            anonymous_cookie, now=current
        )
        return IdentityResolution(
            identity=anonymous.identity,
            cookie_value=anonymous.cookie_value,
            clear_user_cookie=clear_user_cookie,
        )


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def require_scope(identity: ResolvedIdentity, scope: str) -> None:
    if scope not in identity.scopes:
        raise AuthorizationError(f"missing required scope: {scope}")


def _utc(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return current.astimezone(UTC)
