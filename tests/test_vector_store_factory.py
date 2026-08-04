from knowledge.config.settings import Settings
from knowledge.repositories import vector_store_factory
from knowledge.repositories.vector_shadow_repository import ShadowVectorStoreRepository


def test_factory_keeps_chroma_as_the_default(monkeypatch, tmp_path):
    expected = object()
    calls = []

    def create(settings, require_embedding=True, collection_name=None):
        calls.append((settings, require_embedding, collection_name))
        return expected

    monkeypatch.setattr(
        vector_store_factory.VectorStoreRepository,
        "from_settings",
        create,
    )
    settings = Settings(
        _env_file=None,
        VECTOR_STORE_PATH=tmp_path / "chroma",
    )

    result = vector_store_factory.create_vector_store_repository(
        settings,
        require_embedding=False,
        collection_name="knowledge",
    )

    assert result is expected
    assert calls == [(settings, False, "knowledge")]


def test_factory_selects_pgvector_and_passes_existing_embedding(monkeypatch):
    expected = object()
    embedding = object()
    calls = []

    def create(settings, *, collection_name=None, embedding=None):
        calls.append((settings, collection_name, embedding))
        return expected

    monkeypatch.setattr(
        vector_store_factory.PostgresVectorStoreRepository,
        "from_settings",
        create,
    )
    settings = Settings(
        _env_file=None,
        VECTOR_STORE_PROVIDER="pgvector",
        DATABASE_URL="postgresql://agent:secret@db.internal/middle_agent",
    )

    result = vector_store_factory.create_vector_store_repository(
        settings,
        collection_name="knowledge",
        embedding=embedding,
    )

    assert result is expected
    assert calls == [(settings, "knowledge", embedding)]


def test_factory_wraps_chroma_with_pgvector_shadow_without_changing_primary(monkeypatch):
    primary = object()
    shadow = object()
    audit = object()
    monkeypatch.setattr(
        vector_store_factory.VectorStoreRepository,
        "from_settings",
        lambda *_args, **_kwargs: primary,
    )
    monkeypatch.setattr(
        vector_store_factory.PostgresVectorStoreRepository,
        "from_settings",
        lambda *_args, **_kwargs: shadow,
    )
    settings = Settings(
        _env_file=None,
        VECTOR_SHADOW_ENABLED=True,
        DATABASE_URL="postgresql://agent:secret@db.internal/middle_agent",
        EMBEDDING_API_KEY="test-key",
    )

    result = vector_store_factory.create_vector_store_repository(
        settings,
        shadow_audit_sink=audit,
        embedding=object(),
    )

    assert isinstance(result, ShadowVectorStoreRepository)
    assert result.primary is primary
    assert result.shadow is shadow
    assert result.audit_sink is audit
