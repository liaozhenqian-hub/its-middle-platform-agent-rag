import base64
import os

import aiosqlite
import pytest

from knowledge.catalog import (
    CatalogRepository,
    CatalogSecretStore,
    KnowledgeSourceCreate,
    SecretCipher,
    SourceType,
)


def test_secret_cipher_requires_base64_encoded_32_byte_key():
    with pytest.raises(ValueError, match="32-byte"):
        SecretCipher(base64.urlsafe_b64encode(b"short").decode())
    with pytest.raises(ValueError, match="base64"):
        SecretCipher("not base64")


def test_secret_cipher_encrypts_with_context_and_detects_tampering():
    key = base64.urlsafe_b64encode(os.urandom(32)).decode()
    cipher = SecretCipher(key)

    encrypted = cipher.encrypt("swagger-password", context="source-1:basic")

    assert "swagger-password" not in encrypted
    assert cipher.decrypt(encrypted, context="source-1:basic") == "swagger-password"
    with pytest.raises(ValueError, match="decrypt"):
        cipher.decrypt(encrypted, context="source-2:basic")


@pytest.mark.asyncio
async def test_secret_store_round_trips_and_source_only_exposes_configured_flag(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.db")
    await repository.initialize()
    await repository.create_source(
        KnowledgeSourceCreate(
            id="swagger-1",
            space_id="middle-platform",
            domain_id="metric-platform",
            source_type=SourceType.SWAGGER,
            name="指标 Swagger",
            config={"url": "https://internal.example/openapi.json"},
        )
    )
    cipher = SecretCipher(base64.urlsafe_b64encode(os.urandom(32)).decode())
    store = CatalogSecretStore(repository, cipher)

    with pytest.raises(ValueError, match="must not be empty"):
        await store.set("swagger-1", "basic", "")

    await store.set("swagger-1", "basic", "user:password")

    assert await store.get("swagger-1", "basic") == "user:password"
    source = await repository.get_source("swagger-1")
    assert source.credential_configured is True
    assert "password" not in repr(source)

    async with aiosqlite.connect(repository.db_path) as db:
        row = await (
            await db.execute(
                "SELECT encrypted_value FROM encrypted_secrets WHERE source_id=?",
                ("swagger-1",),
            )
        ).fetchone()
    assert row is not None
    assert "user:password" not in row[0]

    assert await store.delete("swagger-1", "basic") is True
    assert await store.get("swagger-1", "basic") is None
    assert (await repository.get_source("swagger-1")).credential_configured is False
