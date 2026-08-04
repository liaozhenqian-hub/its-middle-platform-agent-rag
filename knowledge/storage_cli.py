from __future__ import annotations

from dataclasses import asdict
import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import sqlite3
from typing import Any

import typer
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from psycopg_pool import ConnectionPool

from knowledge.config.settings import get_settings
from knowledge.migrations.chroma_pgvector import ChromaPgvectorMigrator
from knowledge.migrations.langgraph_checkpoints import (
    migrate_checkpoints,
    preview_checkpoint_migration,
)
from knowledge.migrations.storage_state import PostgresMigrationStateStore
from knowledge.migrations.verification import (
    verify_relational_storage,
    verify_vector_repositories,
)
from knowledge.migrations.sqlite_postgres import (
    build_relational_migration_plan,
    migrate_relational_storage,
)
from knowledge.repositories.postgres_vector_store_repository import (
    PostgresVectorStoreRepository,
)
from knowledge.repositories.vector_store_repository import VectorStoreRepository
from knowledge.repositories.vector_shadow_repository import load_postgres_shadow_report
from knowledge.persistence.postgres_urls import postgres_saver_url


app = typer.Typer(help="PostgreSQL and pgvector migration/verification CLI.")


def _echo_json(value: Any) -> None:
    typer.echo(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _collections(settings) -> tuple[tuple[str, str], ...]:
    return (
        ("knowledge", settings.chroma_collection_name),
        ("memory", settings.memory_chroma_collection_name),
    )


def _source(settings, collection_name: str) -> VectorStoreRepository:
    return VectorStoreRepository.from_settings(
        settings,
        require_embedding=False,
        collection_name=collection_name,
    )


def _target(settings, collection_name: str) -> PostgresVectorStoreRepository:
    return PostgresVectorStoreRepository.from_settings(
        settings,
        collection_name=collection_name,
        embedding=None,
    )


def _vector_counts() -> dict[str, int]:
    settings = get_settings()
    return {
        label: _source(settings, collection).count()
        for label, collection in _collections(settings)
    }


def _migrate_vector_collections() -> dict[str, int]:
    settings = get_settings()
    sources = [
        (label, collection, _source(settings, collection))
        for label, collection in _collections(settings)
    ]
    fingerprint = sha256(
        json.dumps(
            [(label, collection, source.count()) for label, collection, source in sources],
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    migrated: dict[str, int] = {}
    targets: list[PostgresVectorStoreRepository] = []
    state = None
    run_id = ""
    try:
        for label, collection, source in sources:
            target = _target(settings, collection)
            targets.append(target)
            if state is None:
                state = PostgresMigrationStateStore(
                    target.pool, schema=settings.database_schema
                )
                run_id = state.begin("vectors", fingerprint)
            start_offset = state.step_offsets(run_id).get(label, 0)
            result = ChromaPgvectorMigrator(
                source.vector_store._collection,
                target,
                collection_name=collection,
                batch_size=settings.pgvector_batch_size,
                expected_dimensions=settings.pgvector_dimensions,
                checkpoint=lambda offset, _count, label=label: state.checkpoint(
                    run_id, label, offset, offset
                ),
            ).run(start_offset=start_offset)
            migrated[label] = result.processed_count
        if state is not None:
            state.complete(run_id, {"collection_counts": {
                label: source.count() for label, _, source in sources
            }})
    finally:
        for target in targets:
            target.close()
    return migrated


def _relational_counts() -> dict[str, int]:
    settings = get_settings()
    counts: dict[str, int] = {}
    connections: dict[str, sqlite3.Connection] = {}
    try:
        for item in build_relational_migration_plan(settings):
            key = str(item.source_path)
            connection = connections.setdefault(key, sqlite3.connect(item.source_path))
            counts[item.table] = int(
                connection.execute(f'SELECT count(*) FROM "{item.table}"').fetchone()[0]
            )
        return counts
    finally:
        for connection in connections.values():
            connection.close()


def _verify_relational(sample_size: int) -> dict[str, Any]:
    report = verify_relational_storage(get_settings(), sample_size=sample_size)
    return {**asdict(report), "passed": report.passed}


def _shadow_report() -> dict[str, Any]:
    settings = get_settings()
    pool = ConnectionPool(
        conninfo=settings.resolved_psycopg_url,
        min_size=1,
        max_size=settings.database_pool_size + settings.database_max_overflow,
        timeout=settings.database_pool_timeout_seconds,
        open=True,
    )
    pool.wait(timeout=settings.database_pool_timeout_seconds)
    try:
        return asdict(load_postgres_shadow_report(pool, schema=settings.pgvector_schema))
    finally:
        pool.close()


async def _migrate_checkpoint_store(*, apply: bool) -> dict[str, Any]:
    settings = get_settings()
    active_after = datetime.now(timezone.utc) - timedelta(
        seconds=settings.bug_graph_interrupt_ttl_seconds
    )
    settings.resolved_bug_graph_db.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(
        str(settings.resolved_bug_graph_db)
    ) as source:
        if not apply:
            result = await preview_checkpoint_migration(
                source, active_after=active_after
            )
            return {"mode": "dry-run", **asdict(result)}
        async with AsyncPostgresSaver.from_conn_string(
            postgres_saver_url(
                settings.resolved_psycopg_url,
                schema=settings.database_schema,
            )
        ) as target:
            await target.setup()
            result = await migrate_checkpoints(
                source, target, active_after=active_after
            )
            return {"mode": "apply", **asdict(result)}


@app.command("migrate-vectors")
def migrate_vectors(
    apply: bool = typer.Option(False, "--apply", help="Write existing Chroma vectors to pgvector."),
) -> None:
    if not apply:
        _echo_json({"mode": "dry-run", "source_counts": _vector_counts()})
        return
    _echo_json({"mode": "apply", "migrated_counts": _migrate_vector_collections()})


@app.command("backfill-document-domains")
def backfill_document_domains(
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Copy stable product-document domain IDs from metadata into the indexed column.",
    ),
) -> None:
    settings = get_settings()
    repository = _target(settings, settings.chroma_collection_name)
    try:
        report = repository.backfill_document_domains(apply=apply)
    finally:
        repository.close()
    _echo_json(
        {
            "mode": "apply" if apply else "dry-run",
            "total": report.total,
            "pending": report.pending,
            "updated": report.updated,
            "by_domain": report.by_domain,
        }
    )


@app.command("migrate-relational")
def migrate_relational(
    apply: bool = typer.Option(False, "--apply", help="Write SQLite business rows to PostgreSQL."),
) -> None:
    if not apply:
        counts = _relational_counts()
        _echo_json({"mode": "dry-run", "table_count": len(counts), "row_count": sum(counts.values())})
        return
    counts = migrate_relational_storage(get_settings())
    _echo_json({"mode": "apply", "table_count": len(counts), "row_count": sum(counts.values())})


@app.command("verify-vectors")
def verify_vectors(sample_size: int = typer.Option(100, min=0, max=10000)) -> None:
    settings = get_settings()
    reports: dict[str, Any] = {}
    for label, collection in _collections(settings):
        source = _source(settings, collection)
        target = _target(settings, collection)
        try:
            reports[label] = asdict(
                verify_vector_repositories(source, target, sample_size=sample_size)
            )
        finally:
            target.close()
    _echo_json({"collections": reports})


@app.command("verify-relational")
def verify_relational(
    sample_size: int = typer.Option(100, min=0, max=10000),
) -> None:
    _echo_json(_verify_relational(sample_size))


@app.command("shadow-report")
def shadow_report() -> None:
    _echo_json(_shadow_report())


@app.command("migrate-checkpoints")
def migrate_checkpoint_store(
    apply: bool = typer.Option(
        False, "--apply", help="Write active SQLite checkpoints to PostgreSQL."
    ),
) -> None:
    selector_policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if selector_policy is not None:
        asyncio.set_event_loop_policy(selector_policy())
    _echo_json(asyncio.run(_migrate_checkpoint_store(apply=apply)))


@app.command("build-vector-index")
def build_vector_index(
    apply: bool = typer.Option(False, "--apply", help="Create HNSW and run ANALYZE."),
) -> None:
    settings = get_settings()
    if not apply:
        _echo_json({"mode": "dry-run", "operation": "hnsw-and-analyze"})
        return
    repository = _target(settings, settings.chroma_collection_name)
    try:
        repository.build_hnsw_index()
        repository.analyze()
    finally:
        repository.close()
    _echo_json({"mode": "apply", "status": "completed"})


def main() -> None:
    app()


if __name__ == "__main__":
    main()
