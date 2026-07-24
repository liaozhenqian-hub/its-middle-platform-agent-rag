from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Protocol

from knowledge.catalog import (
    CatalogRepository,
    ChunkCatalogCreate,
    KnowledgeSourceCreate,
    SourceFileCreate,
    SourceType,
    SourceVersionCreate,
)


LEGACY_SOURCE_ID = "legacy-metric-platform-documents"
LEGACY_VERSION_ID = "legacy-metric-platform-documents-v1"


class ChromaCollection(Protocol):
    def get(self, include: list[str]) -> dict[str, Any]: ...

    def update(self, ids: list[str], metadatas: list[dict[str, Any]]) -> None: ...


@dataclass(frozen=True)
class LegacyMigrationResult:
    candidate_count: int
    updated_count: int
    source_id: str = LEGACY_SOURCE_ID


class LegacyCatalogMigrator:
    def __init__(self, catalog: CatalogRepository, collection: ChromaCollection):
        self.catalog = catalog
        self.collection = collection

    async def run(self, apply: bool = False) -> LegacyMigrationResult:
        records = self.collection.get(include=["documents", "metadatas"])
        candidates = []
        for chunk_id, content, raw_metadata in zip(
            records.get("ids") or [],
            records.get("documents") or [],
            records.get("metadatas") or [],
        ):
            metadata = dict(raw_metadata or {})
            if (
                metadata.get("app_id") == "middle-platform"
                and metadata.get("domain") == "指标平台"
            ):
                candidates.append((str(chunk_id), str(content or ""), metadata))

        if not apply:
            return LegacyMigrationResult(candidate_count=len(candidates), updated_count=0)

        await self._ensure_source()
        file_ids: dict[str, str] = {}
        enriched_metadatas = []
        ids = []
        for chunk_id, content, metadata in candidates:
            source_path = str(metadata.get("source_path") or "legacy.md")
            file_id = file_ids.get(source_path)
            if file_id is None:
                file_id = self._file_id(source_path)
                file_ids[source_path] = file_id
                await self._ensure_file(file_id, source_path, content)
            previous_type = str(metadata.get("legacy_source_type") or metadata.get("source_type") or "")
            enriched = {
                **metadata,
                "space_id": "middle-platform",
                "domain_id": "metric-platform",
                "source_id": LEGACY_SOURCE_ID,
                "source_version": "legacy-v1",
                "source_type": "product_document",
                "legacy_source_type": previous_type,
            }
            await self.catalog.upsert_chunk(
                ChunkCatalogCreate(
                    chunk_id=chunk_id,
                    source_id=LEGACY_SOURCE_ID,
                    version_id=LEGACY_VERSION_ID,
                    source_file_id=file_id,
                    source_type=SourceType.DOCUMENT,
                    domain_key="metric-platform",
                    locator=str(metadata.get("heading") or source_path),
                    content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    metadata={
                        "relative_path": source_path,
                        "heading": str(metadata.get("heading") or ""),
                    },
                )
            )
            ids.append(chunk_id)
            enriched_metadatas.append(enriched)
        if ids:
            self.collection.update(ids=ids, metadatas=enriched_metadatas)
        return LegacyMigrationResult(
            candidate_count=len(candidates),
            updated_count=len(ids),
        )

    async def _ensure_source(self) -> None:
        if await self.catalog.get_source(LEGACY_SOURCE_ID) is None:
            await self.catalog.create_source(
                KnowledgeSourceCreate(
                    id=LEGACY_SOURCE_ID,
                    space_id="middle-platform",
                    domain_id="metric-platform",
                    source_type=SourceType.DOCUMENT,
                    name="现有指标平台产品文档",
                    config={"legacy": True},
                )
            )
        versions = await self.catalog.list_versions(LEGACY_SOURCE_ID)
        if not any(version.id == LEGACY_VERSION_ID for version in versions):
            await self.catalog.create_version(
                SourceVersionCreate(
                    id=LEGACY_VERSION_ID,
                    source_id=LEGACY_SOURCE_ID,
                    version_ref="legacy-v1",
                    status="succeeded",
                    current=True,
                    metadata={"migration": "legacy_catalog_v1"},
                )
            )

    async def _ensure_file(self, file_id: str, source_path: str, content: str) -> None:
        existing = await self.catalog.list_files(
            LEGACY_SOURCE_ID,
            LEGACY_VERSION_ID,
            file_id=file_id,
        )
        if existing:
            return
        await self.catalog.create_file(
            SourceFileCreate(
                id=file_id,
                source_id=LEGACY_SOURCE_ID,
                version_id=LEGACY_VERSION_ID,
                relative_path=source_path,
                domain_key="metric-platform",
                language=None,
                content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                size_bytes=len(content.encode("utf-8")),
                metadata={"legacy": True},
            )
        )

    @staticmethod
    def _file_id(source_path: str) -> str:
        digest = hashlib.sha256(source_path.encode("utf-8")).hexdigest()[:24]
        return f"legacy-file-{digest}"
