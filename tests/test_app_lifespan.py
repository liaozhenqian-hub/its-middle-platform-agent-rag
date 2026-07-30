import base64
import asyncio
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import knowledge.api.app as app_module
from knowledge.config.settings import Settings


@pytest.mark.asyncio
async def test_registry_warmup_runs_without_blocking_application_startup():
    started = Event()
    release = Event()

    class SlowRegistry:
        def warm(self, _scopes):
            started.set()
            release.wait(timeout=2)

    task = app_module._start_registry_warmup(
        SlowRegistry(),
        [("middle-platform", None)],
    )

    assert await asyncio.to_thread(started.wait, 0.5)
    assert not task.done()
    release.set()
    await task


@pytest.mark.asyncio
async def test_registry_warmup_can_be_disabled_for_remote_vector_storage():
    class Registry:
        def warm(self, _scopes):
            raise AssertionError("disabled warmup must not run")

    task = app_module._start_registry_warmup(
        Registry(),
        [("middle-platform", None)],
        enabled=False,
    )

    assert task is None


def test_manager_reasoning_synthesizer_is_only_built_for_enabled_deepseek():
    class FakeModelFactory:
        def __init__(self):
            self.calls = 0

        def create_reasoning_model(self):
            self.calls += 1
            return "pro-model"

        def create_run_config(self, *_args, **_kwargs):
            return None

    model_factory = FakeModelFactory()
    settings = Settings(
        _env_file=None,
        AGENT_MODEL_PROVIDER="deepseek",
        DEEPSEEK_API_KEY="test-key",
        DEEPSEEK_REASONING_ENABLED=True,
        AGENT_MANAGER_REASONING_ENABLED=True,
    )

    synthesizer = app_module._build_manager_reasoning_synthesizer(
        settings,
        model_factory,
    )

    assert synthesizer is not None
    assert synthesizer.agent.model == "pro-model"
    assert model_factory.calls == 1


@pytest.mark.asyncio
async def test_lifespan_waits_for_pending_quality_completion_tasks():
    application = app_module.create_app(agent_service=object())
    completed = asyncio.Event()

    async def finish_quality_capture():
        await asyncio.sleep(0.01)
        completed.set()

    async with application.router.lifespan_context(application):
        task = asyncio.create_task(finish_quality_capture())
        application.state.quality_completion_tasks.add(task)

    assert completed.is_set()
    assert not application.state.quality_completion_tasks


@pytest.mark.parametrize(
    ("provider", "manager_enabled", "reasoning_enabled"),
    [
        ("openai", True, True),
        ("deepseek", False, True),
        ("deepseek", True, False),
    ],
)
def test_manager_reasoning_synthesizer_stays_disabled_when_gate_is_closed(
    provider,
    manager_enabled,
    reasoning_enabled,
):
    class FakeModelFactory:
        def create_reasoning_model(self):
            raise AssertionError("reasoning model must not be created")

    settings = Settings(
        _env_file=None,
        AGENT_MODEL_PROVIDER=provider,
        AGENT_OPENAI_API_KEY="test-key",
        DEEPSEEK_API_KEY="test-key",
        AGENT_MANAGER_REASONING_ENABLED=manager_enabled,
        DEEPSEEK_REASONING_ENABLED=reasoning_enabled,
    )

    assert (
        app_module._build_manager_reasoning_synthesizer(
            settings,
            FakeModelFactory(),
        )
        is None
    )


def test_production_lifespan_wires_catalog_scopes_and_swagger_provider(
    monkeypatch, tmp_path
):
    settings = Settings(
        _env_file=None,
        EMBEDDING_API_KEY="fake",
        VECTOR_STORE_PATH=tmp_path / "chroma",
        KNOWLEDGE_CATALOG_DB=tmp_path / "catalog.db",
        AGENT_SESSION_DB=tmp_path / "agent.db",
        BUG_GRAPH_DB=tmp_path / "bug-graph.db",
        KNOWLEDGE_STORAGE_ROOT=tmp_path / "storage",
        KNOWLEDGE_SECRET_MASTER_KEY=base64.urlsafe_b64encode(b"m" * 32).decode(),
        SOURCE_WORKER_ENABLED=False,
        METRIC_MCP_ENABLED=False,
        AGENT_TRACING_ENABLED=False,
        ADMIN_PASSWORD_HASH="",
        SWAGGER_ALLOWED_HOSTS="swagger.internal",
        GRAFANA_LOG_ENABLED=True,
        GRAFANA_LOG_URL="https://grafana.internal/api/ds/query",
        GRAFANA_LOG_BEARER_TOKEN="secret-token",
        GRAFANA_DEVELOP_DATASOURCE_UID="dev-uid",
        GRAFANA_DEVELOP_NAMESPACE="middle-develop",
        GRAFANA_TEST_DATASOURCE_UID="test-uid",
        GRAFANA_TEST_NAMESPACE="middle-test",
        GRAFANA_PROD_DATASOURCE_UID="prod-uid",
        GRAFANA_PROD_NAMESPACE="middle-prod",
        FEISHU_BOT_ENABLED=True,
        FEISHU_APP_ID="cli_test",
        FEISHU_APP_SECRET="rotated-secret",
        FEISHU_EVENT_DB=tmp_path / "feishu.db",
    )
    captured = {}

    class FakeModelFactory:
        def __init__(self, runtime_settings):
            assert runtime_settings is settings

        def create_model(self):
            return "model"

        def create_run_config(self, conversation_id):
            return None

    class FakeRegistry:
        def __init__(self, settings):
            self.repository = SimpleNamespace(count=lambda: 0)

        def warm(self, scopes):
            captured["warm_scopes"] = scopes

    class FakeMCP:
        def __init__(self, settings):
            self.available = False
            self.server = None
            self.status = "disabled"

        async def connect(self):
            return None

        async def close(self):
            captured["mcp_closed"] = True

    class FakeAgentFactory:
        def __init__(self, **kwargs):
            captured["agent_factory"] = kwargs

        def create(self):
            return SimpleNamespace(manager=object())

    class FakeFeishuGateway:
        def __init__(self, app_id, app_secret):
            assert app_id == "cli_test"
            assert app_secret == "rotated-secret"
            self.connected = True
            captured["feishu_gateway"] = self

    class FakeFeishuRepository:
        def __init__(self, path):
            captured["feishu_db"] = path

        async def initialize(self):
            return None

    original_relational_factory = app_module.RelationalRepositoryFactory

    class FakeRelationalFactory(original_relational_factory):
        def feishu_events(self, path):
            return FakeFeishuRepository(path)

    class FakeFeishuBridge:
        def __init__(self, **kwargs):
            captured["feishu_bridge"] = kwargs
            self.gateway = kwargs["gateway"]

        async def start(self):
            captured["feishu_started"] = True

        async def close(self):
            captured["feishu_closed"] = True

    monkeypatch.setattr(app_module, "Settings", lambda: settings)
    monkeypatch.setattr(app_module, "configure_logging", lambda settings: None)
    monkeypatch.setattr(app_module, "AgentModelFactory", FakeModelFactory)
    monkeypatch.setattr(app_module, "RetrievalPipelineRegistry", FakeRegistry)
    monkeypatch.setattr(app_module, "MetricMCPClient", FakeMCP)
    monkeypatch.setattr(app_module, "AgentFactory", FakeAgentFactory)
    monkeypatch.setattr(app_module, "AgentService", lambda **kwargs: object())
    monkeypatch.setattr(app_module, "LarkOapiGateway", FakeFeishuGateway, raising=False)
    monkeypatch.setattr(app_module, "RelationalRepositoryFactory", FakeRelationalFactory)
    monkeypatch.setattr(app_module, "FeishuBotBridge", FakeFeishuBridge, raising=False)

    application = app_module.create_app()
    with TestClient(application) as client:
        ready = client.get("/health/ready")
        assert ready.status_code == 200
        assert application.state.catalog is not None
        assert application.state.catalog_secret_store is not None
        assert application.state.conversation_scopes is not None
        assert application.state.swagger_inspector is not None
        assert application.state.swagger_source_provider is not None
        assert ready.json()["components"]["grafana_logs"]["status"] == "available"
        assert ready.json()["components"]["bug_graph"]["status"] == "available"
        assert ready.json()["components"]["feishu_bot"]["status"] == "available"
        captured["feishu_gateway"].connected = False
        disconnected = client.get("/health/ready")
        assert disconnected.json()["components"]["feishu_bot"]["status"] == "unavailable"

    assert captured["agent_factory"]["swagger_inspector"] is not None
    assert captured["agent_factory"]["swagger_source_provider"] is not None
    assert captured["agent_factory"]["bug_graph_service"] is not None
    assert captured["mcp_closed"] is True
    assert captured["feishu_started"] is True
    assert captured["feishu_closed"] is True
    assert captured["feishu_db"] == tmp_path / "feishu.db"


def test_production_lifespan_marks_incomplete_grafana_configuration_unavailable(
    monkeypatch, tmp_path
):
    settings = Settings(
        _env_file=None,
        EMBEDDING_API_KEY="fake",
        VECTOR_STORE_PATH=tmp_path / "chroma",
        KNOWLEDGE_CATALOG_DB=tmp_path / "catalog.db",
        AGENT_SESSION_DB=tmp_path / "agent.db",
        BUG_GRAPH_DB=tmp_path / "bug-graph.db",
        KNOWLEDGE_STORAGE_ROOT=tmp_path / "storage",
        SOURCE_WORKER_ENABLED=False,
        METRIC_MCP_ENABLED=False,
        AGENT_TRACING_ENABLED=False,
        GRAFANA_LOG_ENABLED=True,
        GRAFANA_LOG_URL="https://grafana.internal/api/ds/query",
    )
    captured = {}

    class FakeModelFactory:
        def __init__(self, runtime_settings):
            pass

        def create_model(self):
            return "model"

    class FakeRegistry:
        def __init__(self, settings):
            self.repository = SimpleNamespace(count=lambda: 0)

        def warm(self, scopes):
            pass

    class FakeMCP:
        available = False
        server = None
        status = "disabled"

        def __init__(self, settings):
            pass

        async def connect(self):
            pass

        async def close(self):
            pass

    class FakeAgentFactory:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def create(self):
            return SimpleNamespace(manager=object())

    monkeypatch.setattr(app_module, "Settings", lambda: settings)
    monkeypatch.setattr(app_module, "configure_logging", lambda settings: None)
    monkeypatch.setattr(app_module, "AgentModelFactory", FakeModelFactory)
    monkeypatch.setattr(app_module, "RetrievalPipelineRegistry", FakeRegistry)
    monkeypatch.setattr(app_module, "MetricMCPClient", FakeMCP)
    monkeypatch.setattr(app_module, "AgentFactory", FakeAgentFactory)
    monkeypatch.setattr(app_module, "AgentService", lambda **kwargs: object())

    application = app_module.create_app()
    with TestClient(application) as client:
        ready = client.get("/health/ready")

    assert ready.json()["components"]["grafana_logs"]["status"] == "unavailable"
    assert ready.json()["components"]["feishu_bot"]["status"] == "disabled"
    assert captured["bug_graph_service"] is None


def test_production_lifespan_degrades_when_feishu_bridge_start_fails(
    monkeypatch, tmp_path
):
    settings = Settings(
        _env_file=None,
        EMBEDDING_API_KEY="fake",
        VECTOR_STORE_PATH=tmp_path / "chroma",
        KNOWLEDGE_CATALOG_DB=tmp_path / "catalog.db",
        AGENT_SESSION_DB=tmp_path / "agent.db",
        KNOWLEDGE_STORAGE_ROOT=tmp_path / "storage",
        SOURCE_WORKER_ENABLED=False,
        METRIC_MCP_ENABLED=False,
        AGENT_TRACING_ENABLED=False,
        FEISHU_BOT_ENABLED=True,
        FEISHU_APP_ID="cli_test",
        FEISHU_APP_SECRET="rotated-secret",
    )

    class FakeModelFactory:
        def __init__(self, runtime_settings):
            pass

        def create_model(self):
            return "model"

    class FakeRegistry:
        def __init__(self, settings):
            self.repository = SimpleNamespace(count=lambda: 0)

        def warm(self, scopes):
            pass

    class FakeMCP:
        available = False
        server = None
        status = "disabled"

        def __init__(self, settings):
            pass

        async def connect(self):
            pass

        async def close(self):
            pass

    class FakeAgentFactory:
        def __init__(self, **kwargs):
            pass

        def create(self):
            return SimpleNamespace(manager=object())

    class FailingBridge:
        def __init__(self, **kwargs):
            pass

        async def start(self):
            raise RuntimeError("private feishu failure")

        async def close(self):
            pass

    monkeypatch.setattr(app_module, "Settings", lambda: settings)
    monkeypatch.setattr(app_module, "configure_logging", lambda settings: None)
    monkeypatch.setattr(app_module, "AgentModelFactory", FakeModelFactory)
    monkeypatch.setattr(app_module, "RetrievalPipelineRegistry", FakeRegistry)
    monkeypatch.setattr(app_module, "MetricMCPClient", FakeMCP)
    monkeypatch.setattr(app_module, "AgentFactory", FakeAgentFactory)
    monkeypatch.setattr(app_module, "AgentService", lambda **kwargs: object())
    monkeypatch.setattr(app_module, "LarkOapiGateway", lambda *args: object(), raising=False)
    monkeypatch.setattr(app_module, "FeishuEventRepository", lambda path: object(), raising=False)
    monkeypatch.setattr(app_module, "FeishuBotBridge", FailingBridge, raising=False)

    application = app_module.create_app()
    with TestClient(application) as client:
        ready = client.get("/health/ready")

    assert ready.json()["components"]["feishu_bot"]["status"] == "unavailable"


def test_production_lifespan_closes_http_client_when_startup_fails(
    monkeypatch, tmp_path
):
    settings = Settings(
        _env_file=None,
        KNOWLEDGE_CATALOG_DB=tmp_path / "catalog.db",
        AGENT_SESSION_DB=tmp_path / "agent.db",
        SOURCE_WORKER_ENABLED=False,
        METRIC_MCP_ENABLED=False,
        AGENT_TRACING_ENABLED=False,
    )
    closed = []

    class FakeHttpClient:
        async def aclose(self):
            closed.append(True)

    class FailingModelFactory:
        def __init__(self, runtime_settings):
            assert runtime_settings is settings

        def create_model(self):
            raise RuntimeError("model startup failed")

    monkeypatch.setattr(app_module, "Settings", lambda: settings)
    monkeypatch.setattr(app_module, "configure_logging", lambda settings: None)
    monkeypatch.setattr(app_module.httpx, "AsyncClient", FakeHttpClient)
    monkeypatch.setattr(app_module, "AgentModelFactory", FailingModelFactory)

    application = app_module.create_app()
    with pytest.raises(RuntimeError, match="model startup failed"):
        with TestClient(application):
            pass

    assert closed == [True]


def test_production_lifespan_attempts_all_cleanup_in_dependency_order(
    monkeypatch, tmp_path
):
    settings = Settings(
        _env_file=None,
        EMBEDDING_API_KEY="fake",
        VECTOR_STORE_PATH=tmp_path / "chroma",
        KNOWLEDGE_CATALOG_DB=tmp_path / "catalog.db",
        AGENT_SESSION_DB=tmp_path / "agent.db",
        KNOWLEDGE_STORAGE_ROOT=tmp_path / "storage",
        SOURCE_WORKER_ENABLED=True,
        METRIC_MCP_ENABLED=False,
        AGENT_TRACING_ENABLED=False,
        ADMIN_PASSWORD_HASH="",
    )
    cleanup_order = []

    class FakeHttpClient:
        async def aclose(self):
            cleanup_order.append("http")

    class FakeModelFactory:
        def __init__(self, runtime_settings):
            assert runtime_settings is settings

        def create_model(self):
            return "model"

    class FakeRegistry:
        def __init__(self, settings):
            self.repository = SimpleNamespace(count=lambda: 0)

        def warm(self, scopes):
            return None

    class FakeMCP:
        def __init__(self, settings):
            self.available = False
            self.server = None
            self.status = "disabled"

        async def connect(self):
            return None

        async def close(self):
            cleanup_order.append("mcp")
            raise RuntimeError("mcp cleanup failed")

    class FakeWorker:
        def __init__(self, *args, **kwargs):
            pass

        async def start(self):
            return None

        async def stop(self):
            cleanup_order.append("worker")
            raise RuntimeError("worker cleanup failed")

    class FakeAgentFactory:
        def __init__(self, **kwargs):
            pass

        def create(self):
            return SimpleNamespace(manager=object())

    monkeypatch.setattr(app_module, "Settings", lambda: settings)
    monkeypatch.setattr(app_module, "configure_logging", lambda settings: None)
    monkeypatch.setattr(app_module.httpx, "AsyncClient", FakeHttpClient)
    monkeypatch.setattr(app_module, "AgentModelFactory", FakeModelFactory)
    monkeypatch.setattr(app_module, "RetrievalPipelineRegistry", FakeRegistry)
    monkeypatch.setattr(app_module, "MetricMCPClient", FakeMCP)
    monkeypatch.setattr(app_module, "SourceSyncWorker", FakeWorker)
    monkeypatch.setattr(app_module, "AgentFactory", FakeAgentFactory)
    monkeypatch.setattr(app_module, "AgentService", lambda **kwargs: object())

    application = app_module.create_app()
    with pytest.raises(RuntimeError, match="mcp cleanup failed"):
        with TestClient(application):
            pass

    assert cleanup_order == ["worker", "mcp", "http"]
