from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from knowledge.config.settings import Settings


logger = logging.getLogger(__name__)


class DatabaseResources:
    """Owns the optional shared PostgreSQL AsyncEngine without exposing its DSN."""

    def __init__(
        self,
        settings: Settings,
        *,
        engine_factory: Callable[..., AsyncEngine] = create_async_engine,
    ) -> None:
        self.settings = settings
        self._engine_factory = engine_factory
        self._engine: AsyncEngine | Any | None = None
        self.last_error_type: str | None = None

    def __repr__(self) -> str:
        return (
            f"DatabaseResources(enabled={self.enabled!r}, "
            f"started={self.started!r}, schema={self.settings.database_schema!r})"
        )

    @property
    def enabled(self) -> bool:
        return bool(
            self.settings.data_store_provider == "postgres"
            or self.settings.vector_store_provider == "pgvector"
            or self.settings.vector_shadow_enabled
        )

    @property
    def started(self) -> bool:
        return self._engine is not None

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            raise RuntimeError("PostgreSQL resources have not been started")
        return self._engine

    async def start(self) -> None:
        if not self.enabled or self._engine is not None:
            return
        timeout_ms = self.settings.database_statement_timeout_seconds * 1000
        self._engine = self._engine_factory(
            self.settings.resolved_database_url,
            pool_size=self.settings.database_pool_size,
            max_overflow=self.settings.database_max_overflow,
            pool_timeout=self.settings.database_pool_timeout_seconds,
            pool_recycle=self.settings.database_pool_recycle_seconds,
            pool_pre_ping=self.settings.database_pool_pre_ping,
            connect_args={
                "ssl": self.settings.database_ssl_mode,
                "server_settings": {
                    "statement_timeout": str(timeout_ms),
                    "search_path": (
                        f"{self.settings.database_schema},public"
                    ),
                }
            },
        )

    async def check_ready(self) -> bool:
        if self._engine is None:
            return False
        try:
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except Exception as exc:
            self.last_error_type = type(exc).__name__
            logger.warning(
                "PostgreSQL readiness failed error_type=%s",
                self.last_error_type,
            )
            return False
        self.last_error_type = None
        return True

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncConnection]:
        if self._engine is None:
            raise RuntimeError("PostgreSQL resources have not been started")
        async with self._engine.begin() as connection:
            yield connection

    async def close(self) -> None:
        engine = self._engine
        self._engine = None
        if engine is not None:
            await engine.dispose()
