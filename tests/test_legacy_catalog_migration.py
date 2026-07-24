from pathlib import Path

import pytest

from knowledge.catalog import CatalogRepository
from knowledge.migrations.legacy_catalog import LegacyCatalogMigrator


class FakeCollection:
    def __init__(self):
        self.records = {
            "chunk-1": (
                "指标应用正文",
                {
                    "chunk_id": "chunk-1",
                    "app_id": "middle-platform",
                    "domain": "指标平台",
                    "source_type": "faq,backend_code",
                    "source_path": "metric.md",
                    "heading": "指标应用",
                },
            ),
            "other": (
                "其他正文",
                {"chunk_id": "other", "app_id": "other", "domain": "其他"},
            ),
        }
        self.updates = []

    def get(self, include):
        return {
            "ids": list(self.records),
            "documents": [value[0] for value in self.records.values()],
            "metadatas": [value[1] for value in self.records.values()],
        }

    def update(self, ids, metadatas):
        self.updates.append((ids, metadatas))
        for chunk_id, metadata in zip(ids, metadatas):
            content, _ = self.records[chunk_id]
            self.records[chunk_id] = (content, metadata)


@pytest.mark.asyncio
async def test_legacy_migration_dry_run_does_not_mutate_catalog_or_chroma(tmp_path: Path):
    catalog = CatalogRepository(tmp_path / "catalog.db")
    await catalog.initialize()
    collection = FakeCollection()
    migrator = LegacyCatalogMigrator(catalog, collection)

    result = await migrator.run(apply=False)

    assert result.candidate_count == 1
    assert result.updated_count == 0
    assert await catalog.list_sources() == []
    assert collection.updates == []


@pytest.mark.asyncio
async def test_legacy_migration_backfills_metadata_without_reembedding_and_is_idempotent(
    tmp_path: Path,
):
    catalog = CatalogRepository(tmp_path / "catalog.db")
    await catalog.initialize()
    collection = FakeCollection()
    migrator = LegacyCatalogMigrator(catalog, collection)

    first = await migrator.run(apply=True)
    second = await migrator.run(apply=True)

    assert first.updated_count == 1
    assert second.updated_count == 1
    sources = await catalog.list_sources()
    assert len(sources) == 1
    assert sources[0].name == "现有指标平台产品文档"
    chunks = await catalog.list_chunks(source_id=sources[0].id)
    assert [chunk.chunk_id for chunk in chunks] == ["chunk-1"]
    metadata = collection.records["chunk-1"][1]
    assert metadata["space_id"] == "middle-platform"
    assert metadata["domain_id"] == "metric-platform"
    assert metadata["source_type"] == "product_document"
    assert metadata["legacy_source_type"] == "faq,backend_code"
    assert collection.records["other"][1] == {
        "chunk_id": "other",
        "app_id": "other",
        "domain": "其他",
    }
