from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable

from knowledge.auth.identity import hash_secret
from knowledge.auth.models import PersonalApiToken, ResolvedIdentity
from knowledge.auth.repository import UserAuthRepository


ALLOWED_PERSONAL_TOKEN_SCOPES = frozenset({"agent:query", "memory:read"})


@dataclass(frozen=True, slots=True)
class PersonalTokenView:
    id: str
    name: str
    display_prefix: str
    scopes: frozenset[str]
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True, slots=True)
class CreatedPersonalToken:
    plaintext: str
    token: PersonalTokenView


class PersonalTokenService:
    def __init__(
        self,
        repository: UserAuthRepository,
        *,
        secret_factory: Callable[[], str] | None = None,
    ):
        self.repository = repository
        self.secret_factory = secret_factory or (lambda: secrets.token_urlsafe(32))

    async def create(
        self,
        open_id: str,
        *,
        name: str,
        scopes: Iterable[str],
        now: datetime | None = None,
    ) -> CreatedPersonalToken:
        normalized_name = str(name).strip()
        if not normalized_name or len(normalized_name) > 100:
            raise ValueError("token name is required and must not exceed 100 characters")
        normalized_scopes = frozenset(str(scope).strip() for scope in scopes)
        if (
            not normalized_scopes
            or not normalized_scopes <= ALLOWED_PERSONAL_TOKEN_SCOPES
        ):
            raise ValueError("token scope is not allowed")
        plaintext = f"kpat_{self.secret_factory()}"
        stored = await self.repository.create_personal_api_token(
            open_id=open_id,
            name=normalized_name,
            token_hash=hash_secret(plaintext),
            display_prefix=plaintext[:12],
            scopes=normalized_scopes,
            now=now,
        )
        return CreatedPersonalToken(plaintext=plaintext, token=_view(stored))

    async def list(self, open_id: str) -> list[PersonalTokenView]:
        return [
            _view(token)
            for token in await self.repository.list_personal_api_tokens(open_id)
        ]

    async def revoke(
        self, open_id: str, token_id: str, *, now: datetime | None = None
    ) -> bool:
        return await self.repository.revoke_personal_api_token(
            open_id, token_id, now=now
        )

    async def authenticate(
        self, token: str, *, now: datetime | None = None
    ) -> ResolvedIdentity | None:
        stored = await self.repository.get_active_personal_api_token(
            hash_secret(token)
        )
        if stored is None:
            return None
        user = await self.repository.get_feishu_user(stored.open_id)
        if user is None:
            return None
        stored = await self.repository.touch_personal_api_token(stored.id, now=now)
        return ResolvedIdentity(
            owner_id=stored.open_id,
            kind="personal_token",
            display_name=user.display_name,
            scopes=stored.scopes,
            token_id=stored.id,
        )


def _view(token: PersonalApiToken) -> PersonalTokenView:
    return PersonalTokenView(
        id=token.id,
        name=token.name,
        display_prefix=token.display_prefix,
        scopes=token.scopes,
        created_at=token.created_at,
        last_used_at=token.last_used_at,
        revoked_at=token.revoked_at,
    )
