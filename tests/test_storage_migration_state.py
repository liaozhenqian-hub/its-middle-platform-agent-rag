from knowledge.migrations.storage_state import (
    PostgresMigrationStateStore,
    migration_run_id,
    sanitize_migration_summary,
)


def test_migration_run_id_is_stable_and_source_specific():
    first = migration_run_id("vectors", "snapshot-a")
    assert first == migration_run_id("vectors", "snapshot-a")
    assert first != migration_run_id("vectors", "snapshot-b")
    assert first != migration_run_id("relational", "snapshot-a")
    assert len(first) == 40


def test_migration_summary_recursively_removes_secrets_and_content():
    summary = sanitize_migration_summary(
        {
            "processed_count": 500,
            "password": "hidden",
            "DATABASE_URL": "hidden",
            "nested": {
                "embedding": [0.1, 0.2],
                "content": "document body",
                "status": "running",
            },
        }
    )

    assert summary == {
        "processed_count": 500,
        "nested": {"status": "running"},
    }
    assert "hidden" not in repr(summary)
    assert "document body" not in repr(summary)


def test_state_store_persists_only_sanitized_completion_summary():
    captured = []

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def execute(self, statement, params):
            captured.append((statement, params))
            return self
        def fetchone(self): return None

    class Pool:
        def connection(self): return Connection()

    store = PostgresMigrationStateStore(Pool(), schema="public")
    run_id = store.begin("vectors", "snapshot")
    store.checkpoint(run_id, "knowledge", 500, 500)
    store.complete(run_id, {"count": 500, "password": "private"})

    assert len(captured) == 3
    assert "private" not in repr(captured)


def test_state_store_loads_all_step_offsets_in_one_query():
    class Result:
        def fetchall(self): return [("catalog", "500"), ("quality", None)]

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def execute(self, statement, params): return Result()

    class Pool:
        def connection(self): return Connection()

    store = PostgresMigrationStateStore(Pool(), schema="public")

    assert store.step_offsets("run-1") == {"catalog": 500, "quality": 0}
