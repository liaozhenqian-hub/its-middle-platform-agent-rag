from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable

from knowledge.auth.identity import (
    AnonymousIdentityService,
    IdentityResolution,
    RequestIdentityResolver,
    hash_secret,
)
from knowledge.auth.merge import IdentityMergeService
from knowledge.auth.models import OAuthLoginState, ResolvedIdentity, UserSession
from knowledge.auth.oauth import FeishuOAuthClient
from knowledge.auth.ownership import ConversationOwnershipService
from knowledge.auth.repository import UserAuthRepository
from knowledge.auth.tokens import PersonalTokenService
from knowledge.config.settings import Settings


class UserAuthUnavailableError(RuntimeError):
    pass


class InvalidOAuthStateError(RuntimeError):
    pass


class UserCsrfError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LoginStart:
    authorization_url: str
    identity_resolution: IdentityResolution


@dataclass(frozen=True, slots=True)
class LoginResult:
    session_secret: str
    session: UserSession
    redirect_path: str


class UserAuthService:
    def __init__(
        self,
        settings: Settings,
        repository: UserAuthRepository,
        *,
        oauth_client: FeishuOAuthClient | None = None,
        merge_service: IdentityMergeService | None = None,
        token_factory: Callable[[], str] | None = None,
    ):
        self.settings = settings
        self.repository = repository
        self.ownership = ConversationOwnershipService(repository)
        self.token_factory = token_factory or (lambda: secrets.token_urlsafe(32))
        self.anonymous = AnonymousIdentityService(
            repository,
            ttl_seconds=settings.anonymous_device_ttl_seconds,
            token_factory=self.token_factory,
        )
        self.oauth_client = oauth_client
        self.merge_service = merge_service
        self.personal_tokens = PersonalTokenService(repository)
        if oauth_client is None and settings.feishu_oauth_available:
            self.oauth_client = FeishuOAuthClient(
                app_id=settings.resolved_feishu_oauth_app_id,
                app_secret=settings.resolved_feishu_oauth_app_secret,
                callback_url=settings.feishu_oauth_callback_url,
                tenant_key=settings.feishu_tenant_key,
            )
        self.resolver = RequestIdentityResolver(
            repository,
            self.anonymous,
            session_sliding_ttl_seconds=settings.user_session_sliding_ttl_seconds,
            token_authenticator=self.personal_tokens,
        )

    async def resolve(
        self,
        *,
        authorization: str | None,
        user_session_cookie: str | None,
        anonymous_cookie: str | None,
        now: datetime | None = None,
    ) -> IdentityResolution:
        return await self.resolver.resolve(
            authorization=authorization,
            user_session_cookie=user_session_cookie,
            anonymous_cookie=anonymous_cookie,
            now=now,
        )

    async def begin_login(
        self,
        anonymous_cookie: str | None,
        *,
        redirect_path: str = "/chat",
        now: datetime | None = None,
    ) -> LoginStart:
        if not self.settings.feishu_oauth_available or self.oauth_client is None:
            raise UserAuthUnavailableError("Feishu OAuth is unavailable")
        current = _utc(now)
        anonymous = await self.anonymous.resolve(anonymous_cookie, now=current)
        state_secret = self.token_factory()
        await self.repository.create_oauth_state(
            state_hash=hash_secret(state_secret),
            anonymous_owner_id=anonymous.identity.owner_id,
            redirect_path=_safe_redirect_path(redirect_path),
            expires_at=current
            + timedelta(seconds=self.settings.oauth_state_ttl_seconds),
            now=current,
        )
        return LoginStart(
            authorization_url=self.oauth_client.authorization_url(state_secret),
            identity_resolution=anonymous,
        )

    async def complete_login(
        self,
        *,
        code: str,
        state: str,
        anonymous_cookie: str | None,
        now: datetime | None = None,
    ) -> LoginResult:
        if not self.settings.feishu_oauth_available or self.oauth_client is None:
            raise UserAuthUnavailableError("Feishu OAuth is unavailable")
        current = _utc(now)
        stored_state = await self.repository.consume_oauth_state(
            hash_secret(state), now=current
        )
        if stored_state is None or not await self._state_matches_device(
            stored_state, anonymous_cookie, now=current
        ):
            raise InvalidOAuthStateError("OAuth state is invalid")
        profile = await self.oauth_client.authenticate(code)
        await self.repository.upsert_feishu_user(
            open_id=profile.open_id,
            tenant_key=profile.tenant_key,
            display_name=profile.display_name,
            avatar_url=profile.avatar_url,
            now=current,
        )
        session_secret = self.token_factory()
        session = await self.repository.create_user_session(
            token_hash=hash_secret(session_secret),
            open_id=profile.open_id,
            csrf_token=self.token_factory(),
            source_anonymous_owner_id=stored_state.anonymous_owner_id,
            sliding_expires_at=current
            + timedelta(seconds=self.settings.user_session_sliding_ttl_seconds),
            absolute_expires_at=current
            + timedelta(seconds=self.settings.user_session_absolute_ttl_seconds),
            now=current,
        )
        return LoginResult(
            session_secret=session_secret,
            session=session,
            redirect_path=stored_state.redirect_path,
        )

    async def logout(
        self, identity: ResolvedIdentity, csrf_token: str | None
    ) -> None:
        self.validate_user_csrf(identity, csrf_token)
        await self.repository.revoke_user_session(identity.session_id)

    def validate_user_csrf(
        self, identity: ResolvedIdentity, csrf_token: str | None
    ) -> None:
        if identity.kind != "feishu" or not identity.session_id:
            raise UserCsrfError("Feishu user session is required")
        if not csrf_token or not identity.csrf_token or not hmac.compare_digest(
            csrf_token, identity.csrf_token
        ):
            raise UserCsrfError("user CSRF token is invalid")

    async def merge_preview(self, identity: ResolvedIdentity) -> dict[str, object]:
        if identity.kind != "feishu" or not identity.session_id:
            raise UserCsrfError("Feishu user session is required")
        session = await self.repository.get_user_session(identity.session_id)
        source = session.source_anonymous_owner_id
        if not source:
            return {"available": False}
        if self.merge_service is None:
            raise UserAuthUnavailableError("identity merge is unavailable")
        return {
            "available": True,
            **await self.merge_service.preview(source, identity.owner_id),
        }

    async def merge_anonymous(
        self,
        identity: ResolvedIdentity,
        *,
        csrf_token: str | None,
        confirm: bool,
    ) -> dict[str, object]:
        self.validate_user_csrf(identity, csrf_token)
        session = await self.repository.get_user_session(identity.session_id)
        source = session.source_anonymous_owner_id
        if not source:
            return {"status": "not_available"}
        if not confirm:
            await self.repository.clear_user_session_merge_source(session.id)
            return {"status": "declined"}
        if self.merge_service is None:
            raise UserAuthUnavailableError("identity merge is unavailable")
        job = await self.merge_service.merge(source, identity.owner_id)
        await self.repository.clear_user_session_merge_source(session.id)
        return {"status": job.status, "job_id": job.id, "result": job.result}

    async def _state_matches_device(
        self,
        state: OAuthLoginState,
        anonymous_cookie: str | None,
        *,
        now: datetime,
    ) -> bool:
        if not state.anonymous_owner_id or not anonymous_cookie:
            return False
        device = await self.repository.get_active_anonymous_device(
            hash_secret(anonymous_cookie), now=now
        )
        return device is not None and device.owner_id == state.anonymous_owner_id


def _safe_redirect_path(value: str) -> str:
    normalized = str(value).strip()
    if not normalized.startswith("/") or normalized.startswith("//"):
        return "/chat"
    return normalized


def _utc(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return current.astimezone(UTC)
