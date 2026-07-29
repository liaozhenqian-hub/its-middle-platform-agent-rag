from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any

from psycopg import sql
from psycopg_pool import ConnectionPool


@dataclass(frozen=True)
class RelationalTableSnapshot:
    count: int
    primary_key_digests: frozenset[str]
    status_counts: dict[str, int]
    sampled_row_digests: dict[str, str]
    foreign_key_violation_count: int = 0


@dataclass(frozen=True)
class RelationalVerificationReport:
    table_count: int
    missing_target_table_count: int
    extra_target_table_count: int
    count_mismatch_count: int
    primary_key_mismatch_count: int
    status_mismatch_count: int
    hash_mismatch_count: int
    foreign_key_violation_count: int

    @property
    def passed(self) -> bool:
        return all(
            value == 0
            for value in (
                self.missing_target_table_count,
                self.extra_target_table_count,
                self.count_mismatch_count,
                self.primary_key_mismatch_count,
                self.status_mismatch_count,
                self.hash_mismatch_count,
                self.foreign_key_violation_count,
            )
        )


def verify_relational_snapshots(
    source: dict[str, RelationalTableSnapshot],
    target: dict[str, RelationalTableSnapshot],
) -> RelationalVerificationReport:
    common_tables = set(source) & set(target)
    hash_mismatches = 0
    for table in common_tables:
        source_hashes = source[table].sampled_row_digests
        target_hashes = target[table].sampled_row_digests
        sample_keys = set(source_hashes) | set(target_hashes)
        hash_mismatches += sum(
            source_hashes.get(key) != target_hashes.get(key)
            for key in sample_keys
        )
    return RelationalVerificationReport(
        table_count=len(common_tables),
        missing_target_table_count=len(set(source) - set(target)),
        extra_target_table_count=len(set(target) - set(source)),
        count_mismatch_count=sum(
            source[table].count != target[table].count for table in common_tables
        ),
        primary_key_mismatch_count=sum(
            source[table].primary_key_digests
            != target[table].primary_key_digests
            for table in common_tables
        ),
        status_mismatch_count=sum(
            source[table].status_counts != target[table].status_counts
            for table in common_tables
        ),
        hash_mismatch_count=hash_mismatches,
        foreign_key_violation_count=sum(
            target[table].foreign_key_violation_count for table in common_tables
        ),
    )


def capture_sqlite_relational_snapshots(
    plans: Any,
    *,
    primary_keys: dict[str, tuple[str, ...]],
    sample_size: int = 100,
) -> dict[str, RelationalTableSnapshot]:
    snapshots, _ = capture_sqlite_relational_state(
        plans, primary_keys=primary_keys, sample_size=sample_size
    )
    return snapshots


def capture_sqlite_relational_state(
    plans: Any,
    *,
    primary_keys: dict[str, tuple[str, ...]],
    sample_size: int = 100,
) -> tuple[
    dict[str, RelationalTableSnapshot],
    dict[str, list[tuple[Any, ...]]],
]:
    snapshots: dict[str, RelationalTableSnapshot] = {}
    sample_keys: dict[str, list[tuple[Any, ...]]] = {}
    connections: dict[Path, sqlite3.Connection] = {}
    try:
        for plan in plans:
            path = Path(plan.source_path).resolve()
            connection = connections.get(path)
            if connection is None:
                connection = sqlite3.connect(path)
                connection.row_factory = sqlite3.Row
                connections[path] = connection
            rows = connection.execute(f'SELECT * FROM "{plan.table}"').fetchall()
            normalized = [
                _normalize_migration_row(
                    dict(row),
                    column_map=plan.column_map,
                    json_columns=plan.json_columns,
                    boolean_columns=plan.boolean_columns,
                )
                for row in rows
            ]
            snapshot, keys = _snapshot_rows_with_keys(
                normalized,
                primary_keys=primary_keys[plan.table],
                sample_size=sample_size,
            )
            snapshots[plan.table] = snapshot
            sample_keys[plan.table] = keys
        return snapshots, sample_keys
    finally:
        for connection in connections.values():
            connection.close()


def capture_postgres_relational_snapshots(
    pool: Any,
    plans: Any,
    *,
    schema: str,
    primary_keys: dict[str, tuple[str, ...]],
    sample_size: int = 100,
    sample_primary_keys: dict[str, list[tuple[Any, ...]]] | None = None,
) -> dict[str, RelationalTableSnapshot]:
    snapshots: dict[str, RelationalTableSnapshot] = {}
    with pool.connection() as connection:
        for plan in plans:
            if sample_primary_keys is not None:
                snapshots[plan.table] = _capture_postgres_table_optimized(
                    connection,
                    schema=schema,
                    table=plan.table,
                    primary_keys=primary_keys[plan.table],
                    sample_keys=sample_primary_keys.get(plan.table, []),
                )
                continue
            statement = sql.SQL("SELECT * FROM {}.{}").format(
                sql.Identifier(schema), sql.Identifier(plan.table)
            )
            with connection.cursor() as cursor:
                cursor.execute(statement)
                columns = tuple(item.name for item in cursor.description or ())
                rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
            snapshot = _snapshot_rows(
                rows,
                primary_keys=primary_keys[plan.table],
                sample_size=sample_size,
            )
            snapshots[plan.table] = RelationalTableSnapshot(
                count=snapshot.count,
                primary_key_digests=snapshot.primary_key_digests,
                status_counts=snapshot.status_counts,
                sampled_row_digests=snapshot.sampled_row_digests,
                foreign_key_violation_count=_unvalidated_fk_count(
                    connection, schema=schema, table=plan.table
                ),
            )
    return snapshots


def verify_relational_storage(settings: Any, *, sample_size: int = 100):
    from knowledge.migrations.sqlite_postgres import (
        PRIMARY_KEYS,
        build_relational_migration_plan,
    )

    plans = build_relational_migration_plan(settings)
    source, sample_keys = capture_sqlite_relational_state(
        plans, primary_keys=PRIMARY_KEYS, sample_size=sample_size
    )
    pool = ConnectionPool(
        conninfo=settings.resolved_psycopg_url,
        min_size=1,
        max_size=settings.database_pool_size + settings.database_max_overflow,
        timeout=settings.database_pool_timeout_seconds,
        open=True,
    )
    pool.wait(timeout=settings.database_pool_timeout_seconds)
    try:
        target = capture_postgres_relational_snapshots(
            pool,
            plans,
            schema=settings.database_schema,
            primary_keys=PRIMARY_KEYS,
            sample_size=sample_size,
            sample_primary_keys=sample_keys,
        )
    finally:
        pool.close()
    return verify_relational_snapshots(source, target)


def _snapshot_rows(
    rows: list[dict[str, Any]],
    *,
    primary_keys: tuple[str, ...],
    sample_size: int,
) -> RelationalTableSnapshot:
    snapshot, _ = _snapshot_rows_with_keys(
        rows, primary_keys=primary_keys, sample_size=sample_size
    )
    return snapshot


def _snapshot_rows_with_keys(
    rows: list[dict[str, Any]],
    *,
    primary_keys: tuple[str, ...],
    sample_size: int,
) -> tuple[RelationalTableSnapshot, list[tuple[Any, ...]]]:
    keyed: list[tuple[str, str, tuple[Any, ...]]] = []
    statuses: dict[str, int] = {}
    for row in rows:
        key_values = tuple(row.get(key) for key in primary_keys)
        key_digest = _digest(key_values)
        keyed.append((key_digest, _digest(row), key_values))
        if "status" in row and row["status"] is not None:
            status = str(row["status"])
            statuses[status] = statuses.get(status, 0) + 1
    keyed.sort(key=lambda item: item[0])
    selected = keyed[: max(sample_size, 0)]
    sampled = {key: row_hash for key, row_hash, _ in selected}
    snapshot = RelationalTableSnapshot(
        count=len(rows),
        primary_key_digests=frozenset(key for key, _, _ in keyed),
        status_counts=statuses,
        sampled_row_digests=sampled,
    )
    return snapshot, [values for _, _, values in selected]


def _capture_postgres_table_optimized(
    connection: Any,
    *,
    schema: str,
    table: str,
    primary_keys: tuple[str, ...],
    sample_keys: list[tuple[Any, ...]],
) -> RelationalTableSnapshot:
    table_sql = sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier(table))
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL("SELECT * FROM {} LIMIT 0").format(table_sql))
        columns = tuple(item.name for item in cursor.description or ())
    projection = list(primary_keys)
    if "status" in columns:
        projection.append("status")
    statement = sql.SQL("SELECT {} FROM {}").format(
        sql.SQL(", ").join(map(sql.Identifier, projection)), table_sql
    )
    with connection.cursor() as cursor:
        cursor.execute(statement)
        identity_rows = cursor.fetchall()
    key_digests = frozenset(
        _digest(tuple(row[index] for index in range(len(primary_keys))))
        for row in identity_rows
    )
    statuses: dict[str, int] = {}
    if "status" in projection:
        for row in identity_rows:
            value = row[-1]
            if value is not None:
                statuses[str(value)] = statuses.get(str(value), 0) + 1
    sample_rows = _fetch_postgres_sample_rows(
        connection,
        table_sql=table_sql,
        primary_keys=primary_keys,
        sample_keys=sample_keys,
    )
    sampled_hashes = {
        _digest(tuple(row.get(key) for key in primary_keys)): _digest(row)
        for row in sample_rows
    }
    return RelationalTableSnapshot(
        count=len(identity_rows),
        primary_key_digests=key_digests,
        status_counts=statuses,
        sampled_row_digests=sampled_hashes,
        foreign_key_violation_count=_unvalidated_fk_count(
            connection, schema=schema, table=table
        ),
    )


def _fetch_postgres_sample_rows(
    connection: Any,
    *,
    table_sql: Any,
    primary_keys: tuple[str, ...],
    sample_keys: list[tuple[Any, ...]],
) -> list[dict[str, Any]]:
    if not sample_keys:
        return []
    if len(primary_keys) == 1:
        predicate = sql.SQL("{} = ANY(%s)").format(sql.Identifier(primary_keys[0]))
        params: Any = ([values[0] for values in sample_keys],)
    else:
        value_rows = sql.SQL(", ").join(
            sql.SQL("({})").format(
                sql.SQL(", ").join(sql.Placeholder() for _ in primary_keys)
            )
            for _ in sample_keys
        )
        predicate = sql.SQL("({}) IN ({})").format(
            sql.SQL(", ").join(map(sql.Identifier, primary_keys)), value_rows
        )
        params = tuple(value for values in sample_keys for value in values)
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL("SELECT * FROM {} WHERE {}").format(table_sql, predicate),
            params,
        )
        columns = tuple(item.name for item in cursor.description or ())
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _normalize_migration_row(
    row: dict[str, Any],
    *,
    column_map: dict[str, str],
    json_columns: set[str],
    boolean_columns: set[str],
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for source_name, value in row.items():
        target_name = column_map.get(source_name, source_name)
        if target_name in json_columns and isinstance(value, str):
            value = json.loads(value)
        elif target_name in boolean_columns and value is not None:
            value = bool(value)
        output[target_name] = value
    return output


def _digest(value: Any) -> str:
    payload = json.dumps(
        _canonical(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonical(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bytes):
        return {"sha256": sha256(value).hexdigest()}
    if (
        isinstance(value, str)
        and len(value) >= 19
        and value[4:5] == "-"
        and value[7:8] == "-"
        and value[10:11] in {"T", " "}
    ):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    return value


def _unvalidated_fk_count(connection: Any, *, schema: str, table: str) -> int:
    row = connection.execute(
        "SELECT count(*) FROM pg_constraint c "
        "JOIN pg_class t ON t.oid=c.conrelid "
        "JOIN pg_namespace n ON n.oid=t.relnamespace "
        "WHERE c.contype='f' AND NOT c.convalidated "
        "AND n.nspname=%s AND t.relname=%s",
        (schema, table),
    ).fetchone()
    return int(row[0]) if row else 0


@dataclass(frozen=True)
class VectorVerificationReport:
    source_count: int
    target_count: int
    count_matches: bool
    id_set_matches: bool
    missing_in_target_count: int
    extra_in_target_count: int
    sampled_count: int
    hash_mismatch_count: int

    @property
    def passed(self) -> bool:
        return (
            self.count_matches
            and self.id_set_matches
            and self.hash_mismatch_count == 0
        )


def verify_vector_repositories(
    source: Any,
    target: Any,
    *,
    sample_size: int = 100,
) -> VectorVerificationReport:
    source_count = int(source.count())
    target_count = int(target.count())
    source_ids = set(source.get_chunk_ids())
    target_ids = set(target.get_chunk_ids())
    sample_ids = sorted(source_ids & target_ids)[: max(sample_size, 0)]
    source_chunks = {chunk.chunk_id: chunk for chunk in source.get_chunks(ids=sample_ids)}
    target_chunks = {chunk.chunk_id: chunk for chunk in target.get_chunks(ids=sample_ids)}
    mismatches = sum(
        _chunk_hash(source_chunks.get(item)) != _chunk_hash(target_chunks.get(item))
        for item in sample_ids
    )
    return VectorVerificationReport(
        source_count=source_count,
        target_count=target_count,
        count_matches=source_count == target_count,
        id_set_matches=source_ids == target_ids,
        missing_in_target_count=len(source_ids - target_ids),
        extra_in_target_count=len(target_ids - source_ids),
        sampled_count=len(sample_ids),
        hash_mismatch_count=mismatches,
    )


def _chunk_hash(chunk: Any | None) -> str:
    if chunk is None:
        return "missing"
    payload = json.dumps(
        {
            "content": chunk.content,
            "metadata": chunk.metadata,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return sha256(payload).hexdigest()
