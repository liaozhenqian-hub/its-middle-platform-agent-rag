from typer.testing import CliRunner
import asyncio
from types import SimpleNamespace

import knowledge.storage_cli as storage_cli


runner = CliRunner()


def test_storage_cli_lists_safe_migration_commands():
    result = runner.invoke(storage_cli.app, ["--help"])

    assert result.exit_code == 0
    assert "migrate-vectors" in result.stdout
    assert "verify-vectors" in result.stdout
    assert "build-vector-index" in result.stdout
    assert "migrate-relational" in result.stdout
    assert "verify-relational" in result.stdout
    assert "shadow-report" in result.stdout
    assert "migrate-checkpoints" in result.stdout


def test_vector_migration_is_dry_run_by_default(monkeypatch):
    monkeypatch.setattr(storage_cli, "_vector_counts", lambda: {"knowledge": 10, "memory": 1})
    monkeypatch.setattr(
        storage_cli,
        "_migrate_vector_collections",
        lambda: (_ for _ in ()).throw(AssertionError("must not write")),
    )

    result = runner.invoke(storage_cli.app, ["migrate-vectors"])

    assert result.exit_code == 0
    assert '"mode": "dry-run"' in result.stdout
    assert '"knowledge": 10' in result.stdout


def test_relational_verification_cli_outputs_only_safe_aggregate(monkeypatch):
    class Report:
        passed = False

    monkeypatch.setattr(
        storage_cli,
        "_verify_relational",
        lambda sample_size: {
            "passed": False,
            "table_count": 3,
            "hash_mismatch_count": 1,
        },
    )

    result = runner.invoke(storage_cli.app, ["verify-relational", "--sample-size", "25"])

    assert result.exit_code == 0
    assert '"table_count": 3' in result.stdout
    assert '"hash_mismatch_count": 1' in result.stdout
    assert "row body" not in result.stdout


def test_checkpoint_cli_uses_windows_selector_policy_when_available(monkeypatch):
    calls = []

    class Policy: pass

    monkeypatch.setattr(storage_cli.asyncio, "WindowsSelectorEventLoopPolicy", Policy, raising=False)
    monkeypatch.setattr(storage_cli.asyncio, "set_event_loop_policy", lambda policy: calls.append(policy))
    monkeypatch.setattr(storage_cli.asyncio, "run", lambda coroutine: {"mode": "dry-run"})
    monkeypatch.setattr(storage_cli, "_migrate_checkpoint_store", lambda **_kwargs: _Awaitable())

    result = runner.invoke(storage_cli.app, ["migrate-checkpoints"])

    assert result.exit_code == 0
    assert isinstance(calls[0], Policy)


class _Awaitable:
    def __await__(self):
        if False:
            yield None
        return {}


def test_vector_migration_resumes_from_persisted_collection_offset(monkeypatch):
    checkpoints = []
    starts = []
    source = SimpleNamespace(
        vector_store=SimpleNamespace(_collection=object()),
        count=lambda: 5,
    )

    class Target:
        pool = object()
        def close(self): pass

    class State:
        def __init__(self, *_args, **_kwargs): pass
        def begin(self, *_args): return "run"
        def step_offsets(self, _run_id): return {"knowledge": 2}
        def checkpoint(self, _run_id, label, offset, processed):
            checkpoints.append((label, offset, processed))
        def complete(self, *_args): pass

    class Migrator:
        def __init__(self, *_args, checkpoint, **_kwargs): self.checkpoint = checkpoint
        def run(self, *, start_offset):
            starts.append(start_offset)
            self.checkpoint(5, 3)
            return SimpleNamespace(processed_count=3)

    settings = SimpleNamespace(
        chroma_collection_name="collection",
        pgvector_batch_size=500,
        pgvector_dimensions=1024,
        database_schema="schema",
        vector_store_path=SimpleNamespace(resolve=lambda: "path"),
    )
    monkeypatch.setattr(storage_cli, "get_settings", lambda: settings)
    monkeypatch.setattr(storage_cli, "_collections", lambda _settings: (("knowledge", "collection"),))
    monkeypatch.setattr(storage_cli, "_source", lambda *_args: source)
    monkeypatch.setattr(storage_cli, "_target", lambda *_args: Target())
    monkeypatch.setattr(storage_cli, "PostgresMigrationStateStore", State)
    monkeypatch.setattr(storage_cli, "ChromaPgvectorMigrator", Migrator)

    assert storage_cli._migrate_vector_collections() == {"knowledge": 3}
    assert starts == [2]
    assert checkpoints == [("knowledge", 5, 5)]
