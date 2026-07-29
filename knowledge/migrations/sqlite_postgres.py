from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from datetime import datetime
import re
import sqlite3
from typing import Any, Callable, Iterable
from uuid import uuid4

from psycopg import sql
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from knowledge.migrations.storage_state import PostgresMigrationStateStore


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class RelationalTableMigrationResult:
    table: str
    processed_count: int
    last_offset: int


class SqlitePostgresMigrator:
    def __init__(
        self,
        *,
        write_batch: Callable[[str, list[dict[str, Any]]], None],
        checkpoint: Callable[[str, int, int], None] | None = None,
        batch_size: int = 500,
    ) -> None:
        self.write_batch = write_batch
        self.checkpoint = checkpoint
        self.batch_size = batch_size

    def migrate_table(
        self,
        source_path: str | Path,
        table: str,
        *,
        column_map: dict[str, str] | None = None,
        json_columns: set[str] | None = None,
        boolean_columns: set[str] | None = None,
        start_offset: int = 0,
    ) -> RelationalTableMigrationResult:
        mapping = column_map or {}
        json_fields = json_columns or set()
        boolean_fields = boolean_columns or set()
        offset = start_offset
        processed = 0
        with sqlite3.connect(Path(source_path)) as connection:
            connection.row_factory = sqlite3.Row
            while True:
                rows = connection.execute(
                    f'SELECT * FROM "{table}" ORDER BY rowid LIMIT ? OFFSET ?',
                    (self.batch_size, offset),
                ).fetchall()
                if not rows:
                    break
                normalized = [
                    self._normalize_row(
                        row,
                        mapping=mapping,
                        json_fields=json_fields,
                        boolean_fields=boolean_fields,
                    )
                    for row in rows
                ]
                self.write_batch(table, normalized)
                page_count = len(rows)
                offset += page_count
                processed += page_count
                if self.checkpoint is not None:
                    self.checkpoint(table, offset, processed)
        return RelationalTableMigrationResult(
            table=table,
            processed_count=processed,
            last_offset=offset,
        )

    @staticmethod
    def _normalize_row(
        row: sqlite3.Row,
        *,
        mapping: dict[str, str],
        json_fields: set[str],
        boolean_fields: set[str],
    ) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for source_name in row.keys():
            target_name = mapping.get(source_name, source_name)
            value = row[source_name]
            if target_name in json_fields and isinstance(value, str):
                value = json.loads(value)
            elif target_name in boolean_fields and value is not None:
                value = bool(value)
            output[target_name] = value
        return output


class PostgresBatchWriter:
    def __init__(
        self,
        pool: Any,
        *,
        schema: str,
        primary_keys: dict[str, tuple[str, ...]],
    ) -> None:
        if not _IDENTIFIER.fullmatch(schema):
            raise ValueError("invalid PostgreSQL schema")
        self.pool = pool
        self.schema = schema
        self.primary_keys = primary_keys

    def write_batch(self, table: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        if not _IDENTIFIER.fullmatch(table):
            raise ValueError("invalid PostgreSQL table")
        columns = tuple(rows[0])
        if any(not _IDENTIFIER.fullmatch(column) for column in columns):
            raise ValueError("invalid PostgreSQL column")
        if any(tuple(row) != columns for row in rows):
            raise ValueError("migration batch columns are inconsistent")
        keys = self.primary_keys.get(table)
        if not keys:
            raise ValueError(f"primary key mapping is required for {table}")
        updates = [column for column in columns if column not in keys]
        conflict_action = (
            sql.SQL("DO UPDATE SET ")
            + sql.SQL(", ").join(
                sql.SQL("{}=EXCLUDED.{}").format(
                    sql.Identifier(column),
                    sql.Identifier(column),
                )
                for column in updates
            )
            if updates
            else sql.SQL("DO NOTHING")
        )
        staging_table = f"migration_{uuid4().hex}"
        create_staging = sql.SQL(
            "CREATE TEMP TABLE {} (LIKE {}.{} INCLUDING DEFAULTS) ON COMMIT DROP"
        ).format(
            sql.Identifier(staging_table),
            sql.Identifier(self.schema),
            sql.Identifier(table),
        )
        copy_statement = sql.SQL("COPY {} ({}) FROM STDIN").format(
            sql.Identifier(staging_table),
            sql.SQL(", ").join(map(sql.Identifier, columns)),
        )
        statement = sql.SQL(
            "INSERT INTO {}.{} ({}) SELECT {} FROM {} ON CONFLICT ({}) {}"
        ).format(
            sql.Identifier(self.schema),
            sql.Identifier(table),
            sql.SQL(", ").join(map(sql.Identifier, columns)),
            sql.SQL(", ").join(map(sql.Identifier, columns)),
            sql.Identifier(staging_table),
            sql.SQL(", ").join(map(sql.Identifier, keys)),
            conflict_action,
        )
        values = [
            tuple(self._value(row[column]) for column in columns)
            for row in rows
        ]
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(create_staging)
                with cursor.copy(copy_statement) as copy:
                    for value in values:
                        copy.write_row(value)
                cursor.execute(statement)

    @staticmethod
    def _value(value: Any) -> Any:
        if isinstance(value, (dict, list)):
            return Jsonb(value)
        if isinstance(value, str) and "T" in value:
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError:
                return value
            if parsed.tzinfo is not None:
                return parsed
        return value


@dataclass(frozen=True)
class RelationalTablePlan:
    source_path: Path
    table: str
    column_map: dict[str, str]
    json_columns: set[str]
    boolean_columns: set[str]


PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "knowledge_spaces": ("id",), "knowledge_domains": ("id",),
    "knowledge_sources": ("id",), "source_domain_rules": ("id",),
    "source_versions": ("id",), "source_files": ("id",),
    "code_symbols": ("id",), "chunk_catalog": ("chunk_id",),
    "sync_jobs": ("id",), "swagger_cache": ("source_id",),
    "encrypted_secrets": ("source_id", "secret_kind"),
    "source_webhook_secrets": ("source_id",), "admin_sessions": ("id",),
    "audit_events": ("id",), "agent_sessions": ("session_id",),
    "agent_messages": ("id",), "agent_pending_runs": ("run_id",),
    "agent_conversation_scopes": ("conversation_id",),
    "anonymous_devices": ("id",), "feishu_users": ("open_id",),
    "oauth_login_states": ("id",), "user_sessions": ("id",),
    "personal_api_tokens": ("id",), "web_conversation_owners": ("conversation_id",),
    "identity_merge_jobs": ("id",), "auth_audit_events": ("id",),
    "feishu_events": ("event_id",),
    "conversation_memory_summaries": ("conversation_id",),
    "memory_candidates": ("id",), "memories": ("id",),
    "memory_extraction_jobs": ("id",), "memory_conflicts": ("id",),
    "memory_index_repairs": ("memory_id",), "memory_audit_events": ("id",),
    "memory_procedural_specs": ("record_id",), "memory_domain_promotions": ("id",),
    "memory_entities": ("id",), "memory_entity_aliases": ("entity_id", "normalized_alias"),
    "memory_entity_relations": ("id",),
    "memory_entity_evidence": ("relation_id", "source_type", "source_id"),
    "quality_turns": ("id",), "quality_citations": ("id",),
    "quality_tool_runs": ("id",), "quality_feedback": ("id",),
    "quality_spans": ("id",), "quality_annotations": ("id",),
    "eval_cases": ("id",), "eval_runs": ("id",), "eval_results": ("id",),
}


def build_relational_migration_plan(settings: Any) -> tuple[RelationalTablePlan, ...]:
    catalog = settings.resolved_knowledge_catalog_db
    sessions = settings.resolved_agent_session_db
    auth = settings.resolved_user_auth_db
    feishu = settings.resolved_feishu_event_db
    memory = settings.resolved_memory_db
    quality = settings.resolved_agent_quality_db

    json_by_table = {
        "knowledge_sources": {"config_json"}, "source_versions": {"metadata_json"},
        "source_files": {"metadata_json"}, "code_symbols": {"metadata_json"},
        "chunk_catalog": {"metadata_json"}, "swagger_cache": {"specification_json"},
        "audit_events": {"details_json"}, "agent_messages": {"message_data"},
        "agent_pending_runs": {"state", "approvals"}, "personal_api_tokens": {"scopes_json"},
        "identity_merge_jobs": {"result_json"}, "auth_audit_events": {"details_json"},
        "conversation_memory_summaries": {"goals_json", "confirmed_facts_json", "unresolved_items_json", "preferences_json"},
        "memory_candidates": {"source_citations_json"}, "memories": {"source_citations_json"},
        "memory_extraction_jobs": {"source_citations_json"}, "memory_audit_events": {"details_json"},
        "memory_procedural_specs": {name for name in (
            "trigger_conditions_json", "required_inputs_json", "environment_constraints_json",
            "branch_constraints_json", "steps_json", "allowed_tools_json", "stop_conditions_json",
            "fallback_actions_json", "expected_output_json", "validation_steps_json",
        )},
        "quality_turns": {"routed_domains_json", "specialists_used_json"},
        "quality_citations": {"metadata_json"}, "quality_tool_runs": {"arguments_json"},
        "quality_spans": {"metadata_json"}, "quality_annotations": {"details_json"},
        "eval_cases": {"required_tools_json", "required_citation_types_json", "required_facts_json", "forbidden_facts_json", "tags_json", "turns_json"},
        "eval_runs": {"case_ids_json", "config_snapshot_json"},
        "eval_results": {"tool_names_json", "citation_types_json", "checks_json", "judge_json", "failure_codes_json", "case_snapshot_json"},
    }
    bool_by_table = {
        "knowledge_sources": {"enabled"}, "source_domain_rules": {"shared"},
        "source_versions": {"current"}, "memory_conflicts": {"resolved"},
        "eval_cases": {"enabled"}, "eval_runs": {"cancel_requested"},
        "eval_results": {"passed"},
    }
    groups = (
        (catalog, ("knowledge_spaces", "knowledge_domains", "knowledge_sources", "source_domain_rules", "source_versions", "source_files", "code_symbols", "chunk_catalog", "sync_jobs", "swagger_cache", "encrypted_secrets", "source_webhook_secrets", "admin_sessions", "audit_events")),
        (auth, ("anonymous_devices", "feishu_users", "oauth_login_states", "user_sessions", "personal_api_tokens", "web_conversation_owners", "identity_merge_jobs", "auth_audit_events")),
        (sessions, ("agent_sessions", "agent_messages", "agent_pending_runs", "agent_conversation_scopes")),
        (memory, ("conversation_memory_summaries", "memory_candidates", "memories", "memory_extraction_jobs", "memory_conflicts", "memory_index_repairs", "memory_audit_events", "memory_procedural_specs", "memory_domain_promotions", "memory_entities", "memory_entity_aliases", "memory_entity_relations", "memory_entity_evidence")),
        (quality, ("quality_turns", "quality_citations", "quality_tool_runs", "quality_feedback", "quality_spans", "quality_annotations", "eval_cases", "eval_runs", "eval_results")),
        (feishu, ("feishu_events",)),
    )
    output = []
    for path, tables in groups:
        for table in tables:
            output.append(RelationalTablePlan(
                source_path=path,
                table=table,
                column_map=(
                    {"state_json": "state", "approvals_json": "approvals"}
                    if table == "agent_pending_runs" else {}
                ),
                json_columns=set(json_by_table.get(table, set())),
                boolean_columns=set(bool_by_table.get(table, set())),
            ))
    return tuple(output)


def relational_source_fingerprint(plan: Iterable[RelationalTablePlan]) -> str:
    digest_parts = []
    for path in sorted({item.source_path.resolve() for item in plan}):
        stat = path.stat()
        digest_parts.append(f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}")
    from hashlib import sha256

    return sha256("\n".join(digest_parts).encode("utf-8")).hexdigest()


def migrate_relational_storage(settings: Any) -> dict[str, int]:
    plan = build_relational_migration_plan(settings)
    fingerprint = relational_source_fingerprint(plan)
    pool = ConnectionPool(
        conninfo=settings.resolved_psycopg_url,
        min_size=1,
        max_size=settings.database_pool_size + settings.database_max_overflow,
        timeout=settings.database_pool_timeout_seconds,
        open=True,
    )
    pool.wait(timeout=settings.database_pool_timeout_seconds)
    try:
        writer = PostgresBatchWriter(
            pool,
            schema=settings.database_schema,
            primary_keys=PRIMARY_KEYS,
        )
        state = PostgresMigrationStateStore(pool, schema=settings.database_schema)
        run_id = state.begin("relational", fingerprint)
        offsets = state.step_offsets(run_id)
        counts: dict[str, int] = {}
        for item in plan:
            offset = offsets.get(item.table, 0)
            migrator = SqlitePostgresMigrator(
                write_batch=writer.write_batch,
                checkpoint=lambda table, cursor, count, run_id=run_id: state.checkpoint(
                    run_id, table, cursor, cursor
                ),
                batch_size=settings.database_migration_batch_size,
            )
            result = migrator.migrate_table(
                item.source_path,
                item.table,
                column_map=item.column_map,
                json_columns=item.json_columns,
                boolean_columns=item.boolean_columns,
                start_offset=offset,
            )
            counts[item.table] = offset + result.processed_count
        _correct_agent_message_sequence(pool, settings.database_schema)
        state.complete(run_id, {"table_counts": counts})
        return counts
    finally:
        pool.close()


def _correct_agent_message_sequence(pool: Any, schema: str) -> None:
    statement = sql.SQL(
        "SELECT setval(pg_get_serial_sequence(%s, 'id'), "
        "GREATEST(COALESCE((SELECT max(id) FROM {}.agent_messages), 0), 1), true)"
    ).format(sql.Identifier(schema))
    with pool.connection() as connection:
        connection.execute(statement, (f"{schema}.agent_messages",))
