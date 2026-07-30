from contextlib import asynccontextmanager

import pytest

from knowledge.config.settings import Settings
from knowledge.persistence.database import DatabaseResources


class FakeConnection:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.statements = []

    async def execute(self, statement):
        if self.fail:
            raise RuntimeError("database unavailable")
        self.statements.append(str(statement))


class FakeEngine:
    def __init__(self, *, fail_ready=False):
        self.fail_ready = fail_ready
        self.disposed = False
        self.transaction_connection = FakeConnection()

    @asynccontextmanager
    async def connect(self):
        yield FakeConnection(fail=self.fail_ready)

    @asynccontextmanager
    async def begin(self):
        yield self.transaction_connection

    async def dispose(self):
        self.disposed = True


def postgres_settings(**overrides):
    values = {
        "_env_file": None,
        "DATA_STORE_PROVIDER": "postgres",
        "DATABASE_URL": "postgresql://agent:secret@db.internal/middle_agent",
    }
    values.update(overrides)
    return Settings(**values)


@pytest.mark.asyncio
async def test_sqlite_only_resources_remain_disabled():
    resources = DatabaseResources(Settings(_env_file=None))

    await resources.start()

    assert resources.enabled is False
    assert resources.started is False
    assert await resources.check_ready() is False


@pytest.mark.asyncio
async def test_start_builds_secret_safe_async_engine_configuration():
    captured = {}
    engine = FakeEngine()

    def factory(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return engine

    settings = postgres_settings(
        DATABASE_POOL_SIZE=7,
        DATABASE_MAX_OVERFLOW=3,
        DATABASE_POOL_TIMEOUT_SECONDS=12,
        DATABASE_POOL_RECYCLE_SECONDS=45,
        DATABASE_STATEMENT_TIMEOUT_SECONDS=45,
        DATABASE_SSL_MODE="require",
        DATABASE_SCHEMA="agent_test",
    )
    resources = DatabaseResources(settings, engine_factory=factory)

    await resources.start()

    assert resources.enabled is True
    assert resources.started is True
    assert captured["url"].startswith("postgresql+asyncpg://")
    assert captured["kwargs"]["pool_size"] == 7
    assert captured["kwargs"]["max_overflow"] == 3
    assert captured["kwargs"]["pool_timeout"] == 12
    assert captured["kwargs"]["pool_recycle"] == 45
    assert captured["kwargs"]["connect_args"]["server_settings"] == {
        "statement_timeout": "45000",
        "search_path": "agent_test,public",
    }
    assert captured["kwargs"]["connect_args"]["ssl"] == "require"
    assert "secret" not in repr(resources)


@pytest.mark.asyncio
async def test_readiness_and_transaction_use_the_shared_engine():
    engine = FakeEngine()
    resources = DatabaseResources(
        postgres_settings(),
        engine_factory=lambda *_args, **_kwargs: engine,
    )
    await resources.start()

    assert await resources.check_ready() is True
    async with resources.transaction() as connection:
        await connection.execute("select transactional work")

    assert engine.transaction_connection.statements == ["select transactional work"]


@pytest.mark.asyncio
async def test_readiness_failure_is_reported_without_raising_or_leaking_details():
    engine = FakeEngine(fail_ready=True)
    resources = DatabaseResources(
        postgres_settings(),
        engine_factory=lambda *_args, **_kwargs: engine,
    )
    await resources.start()

    assert await resources.check_ready() is False
    assert resources.last_error_type == "RuntimeError"
    assert "database unavailable" not in repr(resources)


@pytest.mark.asyncio
async def test_close_disposes_engine_and_is_idempotent():
    engine = FakeEngine()
    resources = DatabaseResources(
        postgres_settings(),
        engine_factory=lambda *_args, **_kwargs: engine,
    )
    await resources.start()

    await resources.close()
    await resources.close()

    assert engine.disposed is True
    assert resources.started is False
