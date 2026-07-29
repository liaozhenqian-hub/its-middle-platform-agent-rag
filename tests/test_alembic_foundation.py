import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import psycopg
from psycopg import sql
import pytest

from knowledge.config.settings import Settings


pytestmark = pytest.mark.live


@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
    reason="requires the configured dev PostgreSQL",
)
def test_foundation_upgrade_is_idempotent_and_downgrade_removes_tables():
    schema = "agent_migration_test_" + uuid4().hex[:12]
    settings = Settings(
        DATA_STORE_PROVIDER="postgres",
        DATABASE_SCHEMA=schema,
    )
    config = Config(str(Path("alembic.ini").resolve()))
    config.attributes["database_url"] = settings.resolved_psycopg_url
    config.attributes["database_schema"] = schema
    expected = {
        "storage_migration_runs",
        "storage_migration_steps",
        "agent_sessions",
        "agent_messages",
        "agent_pending_runs",
        "agent_conversation_scopes",
        "vector_entries",
    }

    with psycopg.connect(settings.resolved_psycopg_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

    try:
        command.upgrade(config, "head")
        command.upgrade(config, "head")
        with psycopg.connect(settings.resolved_psycopg_url) as connection:
            rows = connection.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = %s",
                (schema,),
            ).fetchall()
            assert expected.issubset({row[0] for row in rows})
            extension = connection.execute(
                "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
            ).fetchone()
            assert extension is not None

        command.downgrade(config, "base")
        with psycopg.connect(settings.resolved_psycopg_url) as connection:
            rows = connection.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = %s",
                (schema,),
            ).fetchall()
            assert {row[0] for row in rows} <= {"alembic_version"}
    finally:
        with psycopg.connect(settings.resolved_psycopg_url, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(schema)
                )
            )
