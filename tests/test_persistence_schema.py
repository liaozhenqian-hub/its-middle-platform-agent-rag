from pgvector.sqlalchemy import VECTOR
from sqlalchemy import Boolean, DateTime
from sqlalchemy.dialects.postgresql import JSONB

from knowledge.persistence.schema import metadata
from knowledge.migrations.sqlite_postgres import PRIMARY_KEYS


def test_foundation_metadata_contains_migration_session_and_vector_tables():
    assert {
        "storage_migration_runs",
        "storage_migration_steps",
        "agent_sessions",
        "agent_messages",
        "agent_pending_runs",
        "agent_conversation_scopes",
        "vector_entries",
    }.issubset(metadata.tables)


def test_vector_entries_use_collection_namespace_and_postgres_native_types():
    table = metadata.tables["vector_entries"]

    assert [column.name for column in table.primary_key.columns] == [
        "collection_name",
        "id",
    ]
    assert isinstance(table.c.metadata.type, JSONB)
    assert isinstance(table.c.embedding.type, VECTOR)
    assert table.c.embedding.type.dim == 1024
    assert isinstance(table.c.enabled.type, Boolean)
    assert isinstance(table.c.created_at.type, DateTime)
    assert table.c.created_at.type.timezone is True


def test_structured_session_and_migration_fields_use_jsonb_and_timestamptz():
    pending = metadata.tables["agent_pending_runs"]
    migration = metadata.tables["storage_migration_runs"]

    assert isinstance(pending.c.state.type, JSONB)
    assert isinstance(pending.c.approvals.type, JSONB)
    assert isinstance(migration.c.summary.type, JSONB)
    assert migration.c.created_at.type.timezone is True


def test_vector_filter_indexes_cover_collection_source_branch_and_owner():
    table = metadata.tables["vector_entries"]
    indexes = {index.name: tuple(column.name for column in index.columns) for index in table.indexes}

    assert indexes["ix_vector_entries_scope"] == (
        "collection_name",
        "app_id",
        "domain",
        "branch",
        "source_type",
        "enabled",
    )
    assert indexes["ix_vector_entries_source"] == (
        "collection_name",
        "source_id",
    )
    assert indexes["ix_vector_entries_memory_scope"] == (
        "collection_name",
        "owner_id",
        "scope_type",
        "space_id",
        "domain",
    )


def test_quality_feedback_uniqueness_matches_existing_sqlite_business_key():
    table = metadata.tables["quality_feedback"]
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }

    assert ("turn_id", "channel", "feedback_key") in unique_columns
    assert ("channel", "feedback_key") not in unique_columns


def test_shared_metadata_covers_every_relational_migration_table():
    assert set(PRIMARY_KEYS).issubset(metadata.tables)
