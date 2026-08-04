from knowledge.agent_runtime.pipeline_registry import RetrievalPipelineRegistry
from knowledge.config.settings import Settings
import knowledge.agent_runtime.pipeline_registry as registry_module
from threading import Event, Thread


def test_pipeline_registry_caches_by_fixed_application_and_domain():
    calls = []

    def build(app_id, domain):
        pipeline = object()
        calls.append((app_id, domain, pipeline))
        return pipeline

    registry = RetrievalPipelineRegistry(pipeline_builder=build)

    first = registry.get("middle-platform", "指标平台")
    second = registry.get("middle-platform", "指标平台")
    other = registry.get("middle-platform", "审批流")

    assert first is second
    assert other is not first
    assert [(app_id, domain) for app_id, domain, _ in calls] == [
        ("middle-platform", "指标平台"),
        ("middle-platform", "审批流"),
    ]


def test_pipeline_registry_atomically_invalidates_affected_cached_pipelines():
    builds = []

    def build(app_id, domain):
        pipeline = object()
        builds.append((app_id, domain, pipeline))
        return pipeline

    registry = RetrievalPipelineRegistry(pipeline_builder=build)
    metric = registry.get("middle-platform", "指标平台")
    approval = registry.get("middle-platform", "审批流")
    unscoped = registry.get("middle-platform", None)

    assert registry.invalidate(app_id="middle-platform", domain="指标平台") == 1
    assert registry.get("middle-platform", "指标平台") is not metric
    assert registry.get("middle-platform", "审批流") is approval

    assert registry.invalidate(app_id="middle-platform") == 3
    assert registry.get("middle-platform", "审批流") is not approval
    assert registry.get("middle-platform", None) is not unscoped


def test_refresh_keeps_old_pipeline_visible_until_replacement_is_ready():
    started = Event()
    release = Event()
    builds = []

    def build(app_id, domain):
        pipeline = object()
        builds.append(pipeline)
        if len(builds) == 2:
            started.set()
            release.wait(timeout=1)
        return pipeline

    registry = RetrievalPipelineRegistry(pipeline_builder=build)
    old = registry.get("middle-platform", None)
    thread = Thread(
        target=lambda: registry.refresh(app_id="middle-platform"),
        daemon=True,
    )

    thread.start()
    assert started.wait(timeout=0.5)
    assert registry.get("middle-platform", None) is old
    release.set()
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert registry.get("middle-platform", None) is builds[1]


def test_refresh_failure_retains_old_pipeline_and_marks_unavailable():
    calls = 0

    def build(_app_id, _domain):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError("private refresh failure")
        return object()

    registry = RetrievalPipelineRegistry(pipeline_builder=build)
    old = registry.get("middle-platform", None)

    assert registry.refresh(app_id="middle-platform") == 0
    assert registry.get("middle-platform", None) is old
    assert registry.warm_status() == {
        "status": "unavailable",
        "cached_pipelines": 1,
    }


def test_refresh_can_fall_back_to_invalidating_cached_pipelines():
    registry = RetrievalPipelineRegistry(
        pipeline_builder=lambda *_args: object(),
        stale_while_refresh_enabled=False,
    )
    old = registry.get("middle-platform", None)

    assert registry.refresh(app_id="middle-platform") == 1
    assert registry.get("middle-platform", None) is not old


def test_warm_status_tracks_successful_warmup():
    registry = RetrievalPipelineRegistry(pipeline_builder=lambda *_args: object())

    assert registry.warm_status()["status"] == "warming"
    registry.warm([("middle-platform", None)])

    assert registry.warm_status() == {
        "status": "available",
        "cached_pipelines": 1,
    }


def test_warm_status_remains_readable_during_initial_pipeline_build():
    build_started = Event()
    release_build = Event()
    status_read = Event()
    observed = {}

    def build(_app_id, _domain):
        build_started.set()
        release_build.wait(timeout=1)
        return object()

    registry = RetrievalPipelineRegistry(pipeline_builder=build)
    warm_thread = Thread(
        target=lambda: registry.warm([("middle-platform", None)]),
        daemon=True,
    )

    def read_status():
        observed.update(registry.warm_status())
        status_read.set()

    status_thread = Thread(target=read_status, daemon=True)
    warm_thread.start()
    assert build_started.wait(timeout=0.5)
    status_thread.start()
    readable_while_building = status_read.wait(timeout=0.2)
    release_build.set()
    warm_thread.join(timeout=1)
    status_thread.join(timeout=1)

    assert readable_while_building is True
    assert observed == {"status": "warming", "cached_pipelines": 0}


def test_concurrent_initial_get_builds_each_scope_once():
    build_started = Event()
    release_build = Event()
    calls = []
    results = []

    def build(app_id, domain):
        calls.append((app_id, domain))
        build_started.set()
        release_build.wait(timeout=1)
        return object()

    registry = RetrievalPipelineRegistry(pipeline_builder=build)
    threads = [
        Thread(
            target=lambda: results.append(registry.get("middle-platform", None)),
            daemon=True,
        )
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    assert build_started.wait(timeout=0.5)
    release_build.set()
    for thread in threads:
        thread.join(timeout=1)

    assert calls == [("middle-platform", None)]
    assert len(results) == 2
    assert results[0] is results[1]


def test_pipeline_registry_uses_the_configured_vector_provider_factory(monkeypatch):
    repository = object()
    calls = []

    def create(settings):
        calls.append(settings.vector_store_provider)
        return repository

    monkeypatch.setattr(registry_module, "create_vector_store_repository", create)
    settings = Settings(
        _env_file=None,
        VECTOR_STORE_PROVIDER="pgvector",
        DATABASE_URL="postgresql://agent:secret@db.internal/middle_agent",
        QUERY_REWRITE_ENABLED=False,
        RERANK_ENABLED=False,
    )

    registry = RetrievalPipelineRegistry(settings=settings)

    assert registry.repository is repository
    assert calls == ["pgvector"]


def test_pipeline_registry_closes_repository_resource():
    class Repository:
        closed = False

        def close(self):
            self.closed = True

    repository = Repository()
    registry = RetrievalPipelineRegistry(
        repository=repository,
        pipeline_builder=lambda *_args: object(),
    )

    registry.close()

    assert repository.closed is True


def test_pipeline_registry_injects_retrieval_performance_switches(monkeypatch):
    captured = {}

    class KeywordService:
        def __init__(self, repository, **kwargs):
            captured["keyword"] = kwargs

    class MultiRouteService:
        def __init__(self, repository, keyword_service, **kwargs):
            captured["multi_route"] = kwargs

    monkeypatch.setattr(registry_module, "KeywordRetrievalService", KeywordService)
    monkeypatch.setattr(registry_module, "MultiRouteRetrievalService", MultiRouteService)
    monkeypatch.setattr(
        registry_module,
        "create_vector_store_repository",
        lambda _settings: object(),
    )
    settings = Settings(
        _env_file=None,
        QUERY_REWRITE_ENABLED=False,
        RERANK_ENABLED=False,
        BM25_MEMORY_FILTER_ENABLED=False,
        RETRIEVAL_PARALLEL_ROUTES_ENABLED=False,
    )

    registry = RetrievalPipelineRegistry(settings=settings)
    registry.get("middle-platform", None)

    assert captured["keyword"]["memory_filter_enabled"] is False
    assert captured["multi_route"]["parallel_routes_enabled"] is False
