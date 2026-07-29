import knowledge.api.app as app_module
from knowledge.config.settings import Settings


def test_bug_graph_checkpointer_uses_postgres_provider(monkeypatch):
    expected = object()
    calls = []

    class Saver:
        @classmethod
        def from_conn_string(cls, value):
            calls.append(value)
            return expected

    monkeypatch.setattr(app_module, "AsyncPostgresSaver", Saver)
    settings = Settings(
        _env_file=None,
        DATA_STORE_PROVIDER="postgres",
        DATABASE_URL="postgresql://user:password@localhost/middle_agent",
    )

    assert app_module._bug_graph_saver_context(settings) is expected
    assert len(calls) == 1
    assert "password" in calls[0]
    assert "search_path" in calls[0]
    assert "public" in calls[0]


def test_bug_graph_checkpointer_keeps_sqlite_default(tmp_path, monkeypatch):
    expected = object()
    calls = []

    class Saver:
        @classmethod
        def from_conn_string(cls, value):
            calls.append(value)
            return expected

    monkeypatch.setattr(app_module, "AsyncSqliteSaver", Saver)
    settings = Settings(_env_file=None, BUG_GRAPH_DB=tmp_path / "bug.db")

    assert app_module._bug_graph_saver_context(settings) is expected
    assert calls == [str(settings.resolved_bug_graph_db)]
