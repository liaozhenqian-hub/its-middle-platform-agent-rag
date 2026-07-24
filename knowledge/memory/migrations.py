from __future__ import annotations

from collections.abc import Iterable

import aiosqlite


async def _columns(database: aiosqlite.Connection, table: str) -> set[str]:
    rows = await (await database.execute(f"PRAGMA table_info({table})")).fetchall()
    return {str(row[1]) for row in rows}


async def _add_columns(
    database: aiosqlite.Connection,
    table: str,
    definitions: Iterable[tuple[str, str]],
) -> None:
    existing = await _columns(database, table)
    for name, definition in definitions:
        if name not in existing:
            await database.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


async def apply_memory_migrations(database: aiosqlite.Connection) -> None:
    await database.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    applied = {
        int(row[0]) for row in await (await database.execute(
            "SELECT version FROM memory_schema_migrations"
        )).fetchall()
    }
    if 1 not in applied:
        await database.execute(
            "INSERT INTO memory_schema_migrations(version,name,applied_at) VALUES(1,'baseline',datetime('now'))"
        )
    if 2 not in applied:
        await database.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_procedural_specs (
                record_id TEXT PRIMARY KEY,
                task_type TEXT NOT NULL,
                procedure_version INTEGER NOT NULL,
                trigger_conditions_json TEXT NOT NULL,
                required_inputs_json TEXT NOT NULL,
                environment_constraints_json TEXT NOT NULL,
                branch_constraints_json TEXT NOT NULL,
                steps_json TEXT NOT NULL,
                allowed_tools_json TEXT NOT NULL,
                minimum_evidence_grade TEXT NOT NULL,
                stop_conditions_json TEXT NOT NULL,
                fallback_actions_json TEXT NOT NULL,
                expected_output_json TEXT NOT NULL,
                validation_steps_json TEXT NOT NULL,
                success_count INTEGER NOT NULL DEFAULT 0,
                failure_count INTEGER NOT NULL DEFAULT 0,
                last_executed_at TEXT,
                reviewed_by TEXT,
                reviewed_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await database.execute(
            "INSERT INTO memory_schema_migrations(version,name,applied_at) VALUES(2,'procedural_specs',datetime('now'))"
        )
    if 3 not in applied:
        await _add_columns(database, "memory_candidates", (
            ("review_state", "TEXT NOT NULL DEFAULT 'pending'"),
            ("review_reason", "TEXT"),
            ("legacy_format", "TEXT"),
        ))
        await _add_columns(database, "memories", (
            ("review_state", "TEXT NOT NULL DEFAULT 'approved'"),
            ("review_reason", "TEXT"),
            ("legacy_format", "TEXT"),
        ))
        await database.executescript(
            """
            CREATE TABLE IF NOT EXISTS memory_domain_promotions (
                id TEXT PRIMARY KEY,
                source_memory_id TEXT NOT NULL,
                target_candidate_id TEXT,
                target_domain_id TEXT NOT NULL,
                public_summary TEXT NOT NULL,
                state TEXT NOT NULL,
                requested_by TEXT NOT NULL,
                reviewed_by TEXT,
                reviewed_at TEXT,
                valid_until TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS memory_conflicts (
                id TEXT PRIMARY KEY,
                memory_id TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                resolved INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                resolved_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_memory_conflicts_active
                ON memory_conflicts(memory_id,resolved,created_at);
            CREATE TABLE IF NOT EXISTS memory_index_repairs (
                memory_id TEXT PRIMARY KEY,
                operation TEXT NOT NULL,
                attempt INTEGER NOT NULL DEFAULT 0,
                last_error_type TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        await database.execute(
            "INSERT INTO memory_schema_migrations(version,name,applied_at) VALUES(3,'governance',datetime('now'))"
        )

