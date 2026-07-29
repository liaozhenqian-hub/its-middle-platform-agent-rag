import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from agents import SQLiteSession
import psycopg
from psycopg import sql
import pytest

from knowledge.agent_runtime.sessions import AgentSessionFactory, PostgresAgentSession
from knowledge.config.settings import Settings
from knowledge.persistence.database import DatabaseResources
import knowledge.api.app as app_module


def test_agent_session_factory_keeps_sqlite_by_default(tmp_path):
    factory = AgentSessionFactory(tmp_path / "sessions.db", 5)

    assert isinstance(factory.create("conversation-1"), SQLiteSession)


def test_postgres_factory_requires_shared_database_resources():
    with pytest.raises(ValueError, match="database_resources"):
        AgentSessionFactory(
            "ignored.db",
            5,
            provider="postgres",
        )


def test_postgres_agent_session_close_is_a_safe_noop():
    session = PostgresAgentSession("conversation-1", object())

    assert session.close() is None


def test_app_session_factory_uses_configured_relational_provider(tmp_path):
    settings = Settings(
        _env_file=None,
        DATA_STORE_PROVIDER="postgres",
        DATABASE_URL="postgresql://user:password@localhost/middle_agent",
        AGENT_SESSION_DB=tmp_path / "sessions.db",
    )
    resources = object()

    factory = app_module._build_agent_session_factory(settings, resources)

    assert factory.provider == "postgres"
    assert factory.database_resources is resources


@pytest.mark.live
@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
    reason="requires the configured dev PostgreSQL",
)
async def test_postgres_agent_session_matches_sdk_history_semantics():
    schema = "agent_session_test_" + uuid4().hex[:12]
    settings = Settings(
        DATA_STORE_PROVIDER="postgres",
        DATABASE_SCHEMA=schema,
    )
    config = Config(str(Path("alembic.ini").resolve()))
    config.attributes["database_url"] = settings.resolved_psycopg_url
    config.attributes["database_schema"] = schema
    with psycopg.connect(settings.resolved_psycopg_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

    resources = DatabaseResources(settings)
    try:
        command.upgrade(config, "head")
        await resources.start()
        factory = AgentSessionFactory(
            "ignored.db",
            2,
            provider="postgres",
            database_resources=resources,
        )
        session = factory.create("conversation-1")
        assert isinstance(session, PostgresAgentSession)

        await session.add_items(
            [
                {"role": "user", "content": "one"},
                {"role": "assistant", "content": "two"},
                {"role": "user", "content": "three"},
            ]
        )
        assert [item["content"] for item in await session.get_items()] == [
            "two",
            "three",
        ]
        assert [item["content"] for item in await session.get_items(limit=1)] == [
            "three"
        ]
        assert (await session.pop_item())["content"] == "three"
        assert [item["content"] for item in await session.get_items(limit=10)] == [
            "one",
            "two",
        ]
        await session.clear_session()
        assert await session.get_items(limit=10) == []
        assert await session.pop_item() is None
    finally:
        await resources.close()
        try:
            command.downgrade(config, "base")
        finally:
            with psycopg.connect(settings.resolved_psycopg_url, autocommit=True) as connection:
                connection.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                        sql.Identifier(schema)
                    )
                )
