import base64
from datetime import UTC, datetime

import pytest

from knowledge.catalog.models import KnowledgeSourceCreate, SourceType
from knowledge.catalog.repository import CatalogRepository
from knowledge.catalog.secrets import CatalogSecretStore, SecretCipher
from knowledge.swagger.catalog import CatalogSwaggerCache, CatalogSwaggerSourceProvider
from knowledge.swagger.inspector import SwaggerCacheEntry


def _master_key() -> str:
    return base64.urlsafe_b64encode(b"k" * 32).decode("ascii")


@pytest.mark.asyncio
async def test_catalog_swagger_provider_decrypts_only_registered_domain_sources(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.db")
    await repository.initialize()
    await repository.create_source(
        KnowledgeSourceCreate(
            id="swagger-metric",
            space_id="middle-platform",
            domain_id="metric-platform",
            source_type=SourceType.SWAGGER,
            name="指标 API",
            config={
                "url": "https://swagger.internal/openapi.json",
                "auth_type": "bearer",
                "timeout_seconds": 12,
            },
        )
    )
    await repository.create_source(
        KnowledgeSourceCreate(
            id="swagger-workflow",
            space_id="middle-platform",
            domain_id="workflow",
            source_type=SourceType.SWAGGER,
            name="工作流 API",
            config={"url": "https://workflow.internal/openapi.json", "auth_type": "none"},
        )
    )
    secret_store = CatalogSecretStore(repository, SecretCipher(_master_key()))
    await secret_store.set("swagger-metric", "bearer_token", "server-secret-token")

    provider = CatalogSwaggerSourceProvider(repository, secret_store)
    sources = await provider.list_for_domain("metric-platform")

    assert len(sources) == 1
    assert sources[0].source_id == "swagger-metric"
    assert sources[0].bearer_token == "server-secret-token"
    public_source = await repository.get_source("swagger-metric")
    assert public_source.credential_configured is True
    assert "server-secret-token" not in str(public_source)


@pytest.mark.asyncio
async def test_catalog_swagger_cache_survives_repository_restart(tmp_path):
    db_path = tmp_path / "catalog.db"
    repository = CatalogRepository(db_path)
    await repository.initialize()
    await repository.create_source(
        KnowledgeSourceCreate(
            id="swagger-1",
            space_id="middle-platform",
            domain_id="metric-platform",
            source_type=SourceType.SWAGGER,
            name="指标 API",
            config={"url": "https://swagger.internal/openapi.json", "auth_type": "none"},
        )
    )
    entry = SwaggerCacheEntry(
        specification={"openapi": "3.0.0", "paths": {}},
        etag='"v1"',
        last_modified="Wed, 15 Jul 2026 00:00:00 GMT",
        refreshed_at=datetime(2026, 7, 15, tzinfo=UTC),
    )
    await CatalogSwaggerCache(repository).put("swagger-1", entry)

    restarted = CatalogSwaggerCache(CatalogRepository(db_path))
    restored = await restarted.get("swagger-1")

    assert restored == entry
