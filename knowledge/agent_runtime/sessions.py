from pathlib import Path
from typing import Any

from agents import SessionABC, SQLiteSession, SessionSettings
from sqlalchemy import delete, func, insert, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.exc import DBAPIError

from knowledge.persistence.database import DatabaseResources
from knowledge.persistence.schema import agent_messages, agent_sessions


class PostgresAgentSession(SessionABC):
    """PostgreSQL implementation of the Agents SDK session contract."""

    def __init__(
        self,
        session_id: str,
        database_resources: DatabaseResources,
        *,
        session_settings: SessionSettings | None = None,
    ) -> None:
        self.session_id = session_id
        self.database_resources = database_resources
        self.session_settings = session_settings

    def _limit(self, limit: int | None) -> int | None:
        if limit is not None:
            return limit
        if self.session_settings is None:
            return None
        return self.session_settings.limit

    async def get_items(self, limit: int | None = None) -> list[dict[str, Any]]:
        resolved_limit = self._limit(limit)
        statement = select(agent_messages.c.message_data).where(
            agent_messages.c.session_id == self.session_id
        )
        reverse = resolved_limit is not None
        if reverse:
            statement = statement.order_by(agent_messages.c.id.desc()).limit(
                resolved_limit
            )
        else:
            statement = statement.order_by(agent_messages.c.id.asc())
        for attempt in range(2):
            try:
                async with self.database_resources.engine.connect() as connection:
                    rows = (await connection.execute(statement)).scalars().all()
                break
            except DBAPIError as exc:
                if attempt or not exc.connection_invalidated:
                    raise
        items = [dict(item) for item in rows if isinstance(item, dict)]
        return list(reversed(items)) if reverse else items

    async def add_items(self, items: list[dict[str, Any]]) -> None:
        if not items:
            return
        async with self.database_resources.transaction() as connection:
            await connection.execute(
                postgres_insert(agent_sessions)
                .values(session_id=self.session_id)
                .on_conflict_do_update(
                    index_elements=[agent_sessions.c.session_id],
                    set_={"updated_at": func.now()},
                )
            )
            await connection.execute(
                insert(agent_messages),
                [
                    {
                        "session_id": self.session_id,
                        "message_data": dict(item),
                    }
                    for item in items
                ],
            )

    async def pop_item(self) -> dict[str, Any] | None:
        latest_id = (
            select(agent_messages.c.id)
            .where(agent_messages.c.session_id == self.session_id)
            .order_by(agent_messages.c.id.desc())
            .limit(1)
            .scalar_subquery()
        )
        statement = (
            delete(agent_messages)
            .where(agent_messages.c.id == latest_id)
            .returning(agent_messages.c.message_data)
        )
        async with self.database_resources.transaction() as connection:
            item = (await connection.execute(statement)).scalar_one_or_none()
        return dict(item) if isinstance(item, dict) else None

    async def clear_session(self) -> None:
        async with self.database_resources.transaction() as connection:
            await connection.execute(
                delete(agent_sessions).where(
                    agent_sessions.c.session_id == self.session_id
                )
            )

    def close(self) -> None:
        """Match SQLiteSession cleanup semantics; connections are per operation."""
        return None


class AgentSessionFactory:
    def __init__(
        self,
        db_path: str | Path,
        history_limit: int,
        *,
        provider: str = "sqlite",
        database_resources: DatabaseResources | None = None,
    ):
        self.db_path = Path(db_path)
        self.history_limit = history_limit
        self.provider = provider
        self.database_resources = database_resources
        if provider == "sqlite":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        elif provider == "postgres":
            if database_resources is None:
                raise ValueError(
                    "database_resources is required for PostgreSQL sessions"
                )
        else:
            raise ValueError("provider must be sqlite or postgres")

    def create(self, conversation_id: str) -> SQLiteSession | PostgresAgentSession:
        settings = SessionSettings(limit=self.history_limit)
        if self.provider == "postgres":
            assert self.database_resources is not None
            return PostgresAgentSession(
                conversation_id,
                self.database_resources,
                session_settings=settings,
            )
        return SQLiteSession(
            session_id=conversation_id,
            db_path=self.db_path,
            session_settings=settings,
        )
