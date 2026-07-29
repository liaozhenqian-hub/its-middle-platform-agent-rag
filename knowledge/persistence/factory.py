from __future__ import annotations

from pathlib import Path

from knowledge.agent_runtime.conversation_scopes import (
    ConversationScopeRepository,
    PostgresConversationScopeRepository,
)
from knowledge.agent_runtime.pending_runs import (
    PendingRunRepository,
    PostgresPendingRunRepository,
)
from knowledge.feishu.repository import (
    FeishuEventRepository,
    PostgresFeishuEventRepository,
)
from knowledge.auth.repository import UserAuthRepository, PostgresUserAuthRepository
from knowledge.catalog.repository import CatalogRepository, PostgresCatalogRepository
from knowledge.memory.repository import MemoryRepository, PostgresMemoryRepository
from knowledge.memory.entities import EntityMemoryRepository, PostgresEntityMemoryRepository
from knowledge.quality.repository import QualityRepository, PostgresQualityRepository
from knowledge.history.service import ConversationHistoryService, PostgresConversationHistoryService
from knowledge.persistence.database import DatabaseResources


class RelationalRepositoryFactory:
    """Select relational implementations without leaking provider branches to callers."""

    def __init__(
        self,
        *,
        provider: str = "sqlite",
        database_resources: DatabaseResources | None = None,
    ) -> None:
        if provider not in {"sqlite", "postgres"}:
            raise ValueError("provider must be sqlite or postgres")
        if provider == "postgres" and database_resources is None:
            raise ValueError("database_resources is required for PostgreSQL repositories")
        self.provider = provider
        self.database_resources = database_resources

    def pending_runs(self, path: str | Path):
        if self.provider == "postgres":
            return PostgresPendingRunRepository(self._postgres())
        return PendingRunRepository(path)

    def conversation_scopes(self, path: str | Path):
        if self.provider == "postgres":
            return PostgresConversationScopeRepository(self._postgres())
        return ConversationScopeRepository(path)

    def feishu_events(self, path: str | Path):
        if self.provider == "postgres":
            return PostgresFeishuEventRepository(self._postgres())
        return FeishuEventRepository(Path(path))

    def user_auth(self, path: str | Path):
        if self.provider == "postgres":
            return PostgresUserAuthRepository(self._postgres())
        return UserAuthRepository(path)

    def catalog(self, path: str | Path):
        if self.provider == "postgres":
            return PostgresCatalogRepository(self._postgres())
        return CatalogRepository(path)

    def memory(self, path: str | Path):
        if self.provider == "postgres":
            return PostgresMemoryRepository(self._postgres())
        return MemoryRepository(path)

    def memory_entities(self, path: str | Path):
        if self.provider == "postgres":
            return PostgresEntityMemoryRepository(self._postgres())
        return EntityMemoryRepository(path)

    def quality(self, path: str | Path):
        if self.provider == "postgres":
            return PostgresQualityRepository(self._postgres())
        return QualityRepository(path)

    def history(self, auth_repository, path: str | Path):
        if self.provider == "postgres":
            return PostgresConversationHistoryService(
                auth_repository,
                self._postgres(),
            )
        return ConversationHistoryService(auth_repository, path)

    def _postgres(self) -> DatabaseResources:
        assert self.database_resources is not None
        return self.database_resources
