from __future__ import annotations

from knowledge.catalog.models import SourceType
from knowledge.catalog.repository import CatalogRepository
from knowledge.catalog.secrets import CatalogSecretStore
from knowledge.swagger.inspector import (
    SwaggerCacheEntry,
    SwaggerSource,
)


class CatalogSwaggerCache:
    def __init__(self, repository: CatalogRepository):
        self._repository = repository

    async def get(self, source_id: str) -> SwaggerCacheEntry | None:
        value = await self._repository.get_swagger_cache(source_id)
        if value is None:
            return None
        return SwaggerCacheEntry(**value)

    async def put(self, source_id: str, entry: SwaggerCacheEntry) -> None:
        await self._repository.put_swagger_cache(
            source_id,
            specification=entry.specification,
            etag=entry.etag,
            last_modified=entry.last_modified,
            refreshed_at=entry.refreshed_at,
        )


class CatalogSwaggerSourceProvider:
    def __init__(
        self,
        repository: CatalogRepository,
        secret_store: CatalogSecretStore | None,
    ):
        self._repository = repository
        self._secret_store = secret_store

    async def list_for_domain(self, domain_id: str) -> list[SwaggerSource]:
        domain_id = domain_id.strip()
        if not domain_id:
            raise ValueError("domain_id is required")
        records = await self._repository.list_sources(
            space_id="middle-platform",
            domain_id=domain_id,
            source_type=SourceType.SWAGGER,
            enabled=True,
        )
        sources = []
        for record in records:
            config = record.config
            auth_type = str(config.get("auth_type") or "none").strip().lower()
            username = ""
            password = ""
            bearer_token = ""
            if self._secret_store is not None:
                if auth_type == "basic":
                    username = await self._secret_store.get(record.id, "username") or ""
                    password = await self._secret_store.get(record.id, "password") or ""
                elif auth_type == "bearer":
                    bearer_token = (
                        await self._secret_store.get(record.id, "bearer_token") or ""
                    )
            sources.append(
                SwaggerSource(
                    source_id=record.id,
                    url=str(config.get("url") or ""),
                    auth_type=auth_type,
                    username=username,
                    password=password,
                    bearer_token=bearer_token,
                    timeout_seconds=float(config.get("timeout_seconds") or 15.0),
                )
            )
        return sources
