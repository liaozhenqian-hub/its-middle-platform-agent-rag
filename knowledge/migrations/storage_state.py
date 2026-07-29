from __future__ import annotations

from hashlib import sha256
from typing import Any

from psycopg import sql
from psycopg.types.json import Jsonb


_SENSITIVE_KEYS = {
    "authorization",
    "content",
    "cookie",
    "database_url",
    "document",
    "dsn",
    "embedding",
    "password",
    "prompt",
    "secret",
    "token",
    "vector",
}


def migration_run_id(migration_type: str, source_fingerprint: str) -> str:
    payload = f"{migration_type.strip()}\0{source_fingerprint.strip()}".encode("utf-8")
    return sha256(payload).hexdigest()[:40]


def sanitize_migration_summary(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            normalized = str(key).casefold().replace("-", "_")
            if any(marker in normalized for marker in _SENSITIVE_KEYS):
                continue
            sanitized[str(key)[:100]] = sanitize_migration_summary(item)
        return sanitized
    if isinstance(value, (list, tuple)):
        return [sanitize_migration_summary(item) for item in value[:100]]
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return type(value).__name__


class PostgresMigrationStateStore:
    def __init__(self, pool: Any, *, schema: str = "public") -> None:
        self.pool = pool
        self.runs = sql.SQL("{}.storage_migration_runs").format(sql.Identifier(schema))
        self.steps = sql.SQL("{}.storage_migration_steps").format(sql.Identifier(schema))

    def begin(self, migration_type: str, source_fingerprint: str) -> str:
        run_id = migration_run_id(migration_type, source_fingerprint)
        statement = sql.SQL(
            "INSERT INTO {} (id,migration_type,source_fingerprint,status,summary) "
            "VALUES (%s,%s,%s,'running','{{}}'::jsonb) "
            "ON CONFLICT (id) DO UPDATE SET status='running',updated_at=now()"
        ).format(self.runs)
        with self.pool.connection() as connection:
            connection.execute(statement, (run_id, migration_type, source_fingerprint))
        return run_id

    def step_offset(self, run_id: str, step_name: str) -> int:
        statement = sql.SQL("SELECT cursor FROM {} WHERE run_id=%s AND step_name=%s").format(self.steps)
        with self.pool.connection() as connection:
            row = connection.execute(statement, (run_id, step_name)).fetchone()
        return int(row[0]) if row and row[0] else 0

    def step_offsets(self, run_id: str) -> dict[str, int]:
        statement = sql.SQL(
            "SELECT step_name,cursor FROM {} WHERE run_id=%s"
        ).format(self.steps)
        with self.pool.connection() as connection:
            rows = connection.execute(statement, (run_id,)).fetchall()
        return {str(name): int(cursor) if cursor else 0 for name, cursor in rows}

    def checkpoint(
        self,
        run_id: str,
        step_name: str,
        offset: int,
        processed_count: int,
    ) -> None:
        statement = sql.SQL(
            "INSERT INTO {} (run_id,step_name,cursor,processed_count,status,summary) "
            "VALUES (%s,%s,%s,%s,'running','{{}}'::jsonb) "
            "ON CONFLICT (run_id,step_name) DO UPDATE SET cursor=EXCLUDED.cursor,"
            "processed_count=EXCLUDED.processed_count,status='running',updated_at=now()"
        ).format(self.steps)
        with self.pool.connection() as connection:
            connection.execute(statement, (run_id, step_name, str(offset), processed_count))

    def complete(self, run_id: str, summary: dict[str, Any]) -> None:
        safe = sanitize_migration_summary(summary)
        statement = sql.SQL(
            "UPDATE {} SET status='completed',summary=%s,completed_at=now(),updated_at=now() WHERE id=%s"
        ).format(self.runs)
        with self.pool.connection() as connection:
            connection.execute(statement, (Jsonb(safe), run_id))
