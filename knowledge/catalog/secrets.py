from __future__ import annotations

import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from knowledge.catalog.repository import CatalogRepository


class SecretCipher:
    """Encrypts source credentials with AES-256-GCM and context-bound AAD."""

    def __init__(self, master_key: str):
        try:
            key = base64.b64decode(master_key, altchars=b"-_", validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("master key must be valid URL-safe base64") from exc
        if len(key) != 32:
            raise ValueError("master key must decode to a 32-byte AES-256 key")
        self._cipher = AESGCM(key)

    def encrypt(self, plaintext: str, *, context: str) -> str:
        nonce = os.urandom(12)
        ciphertext = self._cipher.encrypt(
            nonce,
            plaintext.encode("utf-8"),
            context.encode("utf-8"),
        )
        return base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")

    def decrypt(self, encrypted_value: str, *, context: str) -> str:
        try:
            payload = base64.b64decode(
                encrypted_value, altchars=b"-_", validate=True
            )
            if len(payload) < 28:  # 12-byte nonce + 16-byte authentication tag
                raise ValueError
            plaintext = self._cipher.decrypt(
                payload[:12], payload[12:], context.encode("utf-8")
            )
            return plaintext.decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, InvalidTag, ValueError) as exc:
            raise ValueError("secret could not be decrypted") from exc


class CatalogSecretStore:
    def __init__(self, repository: CatalogRepository, cipher: SecretCipher):
        self._repository = repository
        self._cipher = cipher

    async def set(self, source_id: str, secret_kind: str, plaintext: str) -> None:
        if not secret_kind.strip():
            raise ValueError("secret_kind must not be empty")
        if not plaintext:
            raise ValueError("secret value must not be empty")
        encrypted = self._cipher.encrypt(
            plaintext, context=self._context(source_id, secret_kind)
        )
        await self._repository._set_encrypted_secret(
            source_id, secret_kind, encrypted
        )

    async def get(self, source_id: str, secret_kind: str) -> str | None:
        encrypted = await self._repository._get_encrypted_secret(
            source_id, secret_kind
        )
        if encrypted is None:
            return None
        return self._cipher.decrypt(
            encrypted, context=self._context(source_id, secret_kind)
        )

    async def delete(self, source_id: str, secret_kind: str) -> bool:
        return await self._repository._delete_encrypted_secret(
            source_id, secret_kind
        )

    async def delete_all(self, source_id: str) -> int:
        return await self._repository._delete_all_encrypted_secrets(source_id)

    @staticmethod
    def _context(source_id: str, secret_kind: str) -> str:
        return f"{source_id}:{secret_kind}"
