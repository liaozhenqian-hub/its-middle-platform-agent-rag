from knowledge.migrations.chroma_pgvector import ChromaPgvectorMigrator
import pytest


class _Collection:
    def __init__(self):
        self.calls = []
        self.rows = [
            ("chunk-a", "text-a", {"heading": "A"}, [0.1, 0.2]),
            ("chunk-b", "text-b", {"heading": "B"}, [0.3, 0.4]),
            ("chunk-c", "text-c", {"heading": "C"}, [0.5, 0.6]),
        ]

    def get(self, *, include, limit, offset):
        self.calls.append((tuple(include), limit, offset))
        page = self.rows[offset : offset + limit]
        return {
            "ids": [row[0] for row in page],
            "documents": [row[1] for row in page],
            "metadatas": [row[2] for row in page],
            "embeddings": [row[3] for row in page],
        }


class _Target:
    def __init__(self):
        self.batches = []

    def upsert_with_embeddings(self, chunks, embeddings):
        self.batches.append((chunks, embeddings))
        return [chunk.chunk_id for chunk in chunks]


def test_chroma_migration_pages_existing_embeddings_without_embedding_api_calls():
    source = _Collection()
    target = _Target()
    checkpoints = []
    migrator = ChromaPgvectorMigrator(
        source,
        target,
        collection_name="metric_platform_knowledge",
        batch_size=2,
        expected_dimensions=2,
        checkpoint=lambda offset, count: checkpoints.append((offset, count)),
    )

    result = migrator.run()

    assert result.processed_count == 3
    assert result.last_offset == 3
    assert [[chunk.chunk_id for chunk in chunks] for chunks, _ in target.batches] == [
        ["chunk-a", "chunk-b"],
        ["chunk-c"],
    ]
    assert checkpoints == [(2, 2), (3, 3)]
    assert source.calls == [
        (("documents", "metadatas", "embeddings"), 2, 0),
        (("documents", "metadatas", "embeddings"), 2, 2),
        (("documents", "metadatas", "embeddings"), 2, 3),
    ]
    assert "text-a" not in repr(result)
    assert "0.1" not in repr(result)


def test_chroma_migration_resumes_from_offset_and_rejects_wrong_dimensions():
    source = _Collection()
    target = _Target()
    source.rows[2] = ("chunk-c", "text-c", {}, [0.5])
    migrator = ChromaPgvectorMigrator(
        source,
        target,
        collection_name="middle_platform_memories",
        batch_size=2,
        expected_dimensions=2,
    )

    try:
        migrator.run(start_offset=2)
    except ValueError as exc:
        assert "chunk-c" not in str(exc)
        assert "embedding dimension" in str(exc)
    else:
        raise AssertionError("invalid dimensions must fail")


def test_chroma_migration_accepts_numpy_style_embedding_arrays():
    class ArrayLike:
        def __init__(self, values): self.values = values
        def __iter__(self): return iter(self.values)
        def __bool__(self): raise ValueError("truth value is ambiguous")

    source = _Collection()
    original_get = source.get

    def get(**kwargs):
        records = original_get(**kwargs)
        records["embeddings"] = ArrayLike(records["embeddings"])
        return records

    source.get = get
    target = _Target()

    result = ChromaPgvectorMigrator(
        source,
        target,
        collection_name="knowledge",
        batch_size=3,
        expected_dimensions=2,
    ).run()

    assert result.processed_count == 3


def test_chroma_migration_sanitizes_vector_database_errors():
    source = _Collection()

    class BrokenTarget:
        def upsert_with_embeddings(self, *_args):
            raise RuntimeError("raw embedding [0.1, 0.2] chunk-a")

    migrator = ChromaPgvectorMigrator(
        source,
        BrokenTarget(),
        collection_name="knowledge",
        expected_dimensions=2,
    )

    with pytest.raises(RuntimeError, match="vector batch write failed") as exc:
        migrator.run()
    assert "chunk-a" not in str(exc.value)
    assert "0.1" not in str(exc.value)


def test_chroma_migration_reuses_repository_bulk_import_session():
    source = _Collection()
    calls = []

    class Bulk:
        def __enter__(self): calls.append("enter"); return self
        def __exit__(self, *_args): calls.append("exit")
        def upsert_with_embeddings(self, chunks, embeddings):
            calls.append(len(chunks))

    class Target:
        def bulk_import(self): return Bulk()
        def upsert_with_embeddings(self, *_args):
            raise AssertionError("direct batch write must not be used")

    result = ChromaPgvectorMigrator(
        source,
        Target(),
        collection_name="knowledge",
        batch_size=2,
        expected_dimensions=2,
    ).run()

    assert result.processed_count == 3
    assert calls == ["enter", 2, 1, "exit"]
