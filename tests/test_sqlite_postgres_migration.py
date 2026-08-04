import sqlite3

from knowledge.migrations.sqlite_postgres import (
    PostgresBatchWriter,
    SqlitePostgresMigrator,
    build_relational_migration_plan,
)
from knowledge.config.settings import Settings


def test_relational_migration_batches_maps_columns_and_resumes(tmp_path):
    source = tmp_path / "agent.db"
    with sqlite3.connect(source) as connection:
        connection.execute(
            "CREATE TABLE agent_pending_runs(run_id TEXT PRIMARY KEY,state_json TEXT,approvals_json TEXT)"
        )
        connection.executemany(
            "INSERT INTO agent_pending_runs VALUES(?,?,?)",
            [
                ("run-1", '{"step":1}', "[]"),
                ("run-2", '{"step":2}', "[]"),
                ("run-3", '{"step":3}', "[]"),
            ],
        )

    batches = []
    checkpoints = []
    migrator = SqlitePostgresMigrator(
        write_batch=lambda table, rows: batches.append((table, rows)),
        checkpoint=lambda table, offset, count: checkpoints.append((table, offset, count)),
        batch_size=2,
    )

    result = migrator.migrate_table(
        source,
        "agent_pending_runs",
        column_map={"state_json": "state", "approvals_json": "approvals"},
        json_columns={"state", "approvals"},
        start_offset=1,
    )

    assert result.processed_count == 2
    assert list(batches[0][1][0]) == ["run_id", "state", "approvals"]
    assert batches[0][1][0]["state"] == {"step": 2}
    assert checkpoints == [("agent_pending_runs", 3, 2)]
    assert "step" not in repr(result)


def test_relational_migration_report_never_contains_encrypted_values(tmp_path):
    source = tmp_path / "catalog.db"
    with sqlite3.connect(source) as connection:
        connection.execute(
            "CREATE TABLE encrypted_secrets(source_id TEXT,secret_kind TEXT,encrypted_value TEXT)"
        )
        connection.execute(
            "INSERT INTO encrypted_secrets VALUES('source-1','git','ciphertext-private')"
        )
    migrator = SqlitePostgresMigrator(write_batch=lambda *_args: None)

    result = migrator.migrate_table(source, "encrypted_secrets")

    assert result.processed_count == 1
    assert "ciphertext-private" not in repr(result)


def test_postgres_batch_writer_uses_copy_staging_and_idempotent_upsert():
    captured = []

    class Copy:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def write_row(self, row): captured.append(("row", row))

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def execute(self, statement): captured.append(("execute", statement))
        def copy(self, statement):
            captured.append(("copy", statement))
            return Copy()

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def cursor(self): return Cursor()

    class Pool:
        def connection(self): return Connection()

    writer = PostgresBatchWriter(
        Pool(),
        schema="migration_test",
        primary_keys={"records": ("id",)},
    )
    writer.write_batch("records", [{"id": "a", "status": "queued"}])

    statements = [item[1].as_string() for item in captured if item[0] in {"execute", "copy"}]
    assert any("CREATE TEMP TABLE" in statement for statement in statements)
    assert any("COPY" in statement for statement in statements)
    assert any("ON CONFLICT" in statement for statement in statements)
    assert any("migration_test" in statement for statement in statements)
    assert ("row", ("a", "queued")) in captured


def test_relational_plan_covers_business_tables_and_excludes_sqlite_migration_markers(tmp_path):
    settings = Settings(
        _env_file=None,
        KNOWLEDGE_CATALOG_DB=tmp_path / "catalog.db",
        AGENT_SESSION_DB=tmp_path / "agent.db",
        MEMORY_DB=tmp_path / "memory.db",
        AGENT_QUALITY_DB=tmp_path / "quality.db",
        USER_AUTH_DB=tmp_path / "auth.db",
        FEISHU_EVENT_DB=tmp_path / "feishu.db",
    )
    tables = {item.table for item in build_relational_migration_plan(settings)}

    assert {"knowledge_sources", "agent_messages", "memories", "eval_cases", "feishu_events"} <= tables
    assert not {"schema_migrations", "quality_schema_migrations", "user_auth_schema_migrations"} & tables
