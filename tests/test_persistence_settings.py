import pytest
from pydantic import ValidationError

from knowledge.config.settings import Settings


def test_persistence_defaults_keep_existing_local_providers():
    settings = Settings(_env_file=None)

    assert settings.data_store_provider == "sqlite"
    assert settings.vector_store_provider == "chroma"
    assert settings.database_schema == "public"
    assert settings.pgvector_table == "vector_entries"
    assert settings.pgvector_batch_size == 500
    assert settings.database_migration_batch_size == 5000
    assert settings.pgvector_hnsw_ef_search == 100


def test_database_url_takes_precedence_and_normalizes_async_driver():
    settings = Settings(
        _env_file=None,
        DATA_STORE_PROVIDER="postgres",
        DATABASE_URL="postgresql://preferred:secret@db.internal:5432/middle_agent",
        PGHOST="ignored.internal",
        PGDATABASE="ignored",
        PGUSER="ignored",
        PGPASSWORD="ignored-secret",
    )

    assert settings.resolved_database_url == (
        "postgresql+asyncpg://preferred:secret@db.internal:5432/middle_agent"
    )
    assert settings.resolved_psycopg_url == (
        "postgresql://preferred:secret@db.internal:5432/middle_agent"
    )


def test_split_pg_variables_are_safely_percent_encoded():
    settings = Settings(
        _env_file=None,
        DATA_STORE_PROVIDER="postgres",
        PGHOST="db.internal",
        PGPORT=5432,
        PGDATABASE="middle_agent",
        PGUSER="agent user",
        PGPASSWORD="p@ss:/?#[]&*",
    )

    assert settings.resolved_database_url == (
        "postgresql+asyncpg://agent%20user:p%40ss%3A%2F%3F%23%5B%5D%26%2A"
        "@db.internal:5432/middle_agent"
    )
    assert "p@ss:/?#[]&*" not in repr(settings)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("DATA_STORE_PROVIDER", "mysql"),
        ("VECTOR_STORE_PROVIDER", "faiss"),
        ("DATABASE_SCHEMA", "public;drop schema public"),
        ("PGVECTOR_TABLE", "vector-entries"),
        ("DATABASE_POOL_SIZE", 0),
        ("DATABASE_MAX_OVERFLOW", -1),
        ("DATABASE_POOL_TIMEOUT_SECONDS", 0),
        ("DATABASE_STATEMENT_TIMEOUT_SECONDS", 0),
        ("VECTOR_SHADOW_SAMPLE_RATE", 0),
        ("VECTOR_SHADOW_SAMPLE_RATE", 1.1),
        ("PGVECTOR_BATCH_SIZE", 0),
        ("DATABASE_MIGRATION_BATCH_SIZE", 0),
        ("PGVECTOR_HNSW_EF_SEARCH", 0),
    ],
)
def test_invalid_persistence_configuration_is_rejected(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_postgres_provider_requires_complete_connection_configuration():
    with pytest.raises(ValidationError, match="PostgreSQL configuration"):
        Settings(
            _env_file=None,
            DATA_STORE_PROVIDER="postgres",
            PGHOST="db.internal",
            PGDATABASE="middle_agent",
        )


@pytest.mark.parametrize(
    "configuration",
    [
        {"VECTOR_STORE_PROVIDER": "pgvector"},
        {"VECTOR_SHADOW_ENABLED": True},
    ],
)
def test_vector_postgres_modes_require_connection_configuration(configuration):
    with pytest.raises(ValidationError, match="PostgreSQL configuration"):
        Settings(_env_file=None, **configuration)


def test_pgvector_requires_the_fixed_embedding_dimension():
    with pytest.raises(ValidationError, match="1024"):
        Settings(
            _env_file=None,
            VECTOR_STORE_PROVIDER="pgvector",
            EMBEDDING_DIMENSIONS=1536,
            DATABASE_URL="postgresql://agent:secret@db.internal/middle_agent",
        )
