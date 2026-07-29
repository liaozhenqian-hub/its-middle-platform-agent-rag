from knowledge.agent_runtime.pipeline_registry import RetrievalPipelineRegistry
from knowledge.config.settings import Settings
import knowledge.agent_runtime.pipeline_registry as registry_module


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
