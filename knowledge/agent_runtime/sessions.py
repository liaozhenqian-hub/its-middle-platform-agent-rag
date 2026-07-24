from pathlib import Path

from agents import SQLiteSession, SessionSettings


class AgentSessionFactory:
    def __init__(self, db_path: str | Path, history_limit: int):
        self.db_path = Path(db_path)
        self.history_limit = history_limit
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def create(self, conversation_id: str) -> SQLiteSession:
        return SQLiteSession(
            session_id=conversation_id,
            db_path=self.db_path,
            session_settings=SessionSettings(limit=self.history_limit),
        )
