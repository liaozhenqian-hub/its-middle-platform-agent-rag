from __future__ import annotations

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Float,
    Identity,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB


metadata = MetaData()


storage_migration_runs = Table(
    "storage_migration_runs",
    metadata,
    Column("id", Text, primary_key=True),
    Column("migration_type", Text, nullable=False),
    Column("source_fingerprint", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("summary", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("completed_at", DateTime(timezone=True)),
)


storage_migration_steps = Table(
    "storage_migration_steps",
    metadata,
    Column(
        "run_id",
        Text,
        ForeignKey("storage_migration_runs.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("step_name", Text, primary_key=True),
    Column("cursor", Text),
    Column("processed_count", BigInteger, nullable=False, server_default="0"),
    Column("status", Text, nullable=False),
    Column("summary", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)


agent_sessions = Table(
    "agent_sessions",
    metadata,
    Column("session_id", Text, primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)


agent_messages = Table(
    "agent_messages",
    metadata,
    Column("id", BigInteger, Identity(always=False), primary_key=True),
    Column(
        "session_id",
        Text,
        ForeignKey("agent_sessions.session_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("message_data", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
Index("ix_agent_messages_session_order", agent_messages.c.session_id, agent_messages.c.id)


agent_pending_runs = Table(
    "agent_pending_runs",
    metadata,
    Column("run_id", Text, primary_key=True),
    Column("conversation_id", Text, nullable=False),
    Column("state", JSONB, nullable=False),
    Column("approvals", JSONB, nullable=False),
    Column("status", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
Index(
    "ix_agent_pending_runs_conversation_status",
    agent_pending_runs.c.conversation_id,
    agent_pending_runs.c.status,
)


agent_conversation_scopes = Table(
    "agent_conversation_scopes",
    metadata,
    Column("conversation_id", Text, primary_key=True),
    Column("knowledge_space_id", Text, nullable=False),
    Column("domain_id", Text),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)


feishu_events = Table(
    "feishu_events",
    metadata,
    Column("event_id", Text, primary_key=True),
    Column("message_id", Text, nullable=False),
    Column("chat_id", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("attempt", Integer, nullable=False, server_default="1"),
    Column("error_type", Text),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
Index("ix_feishu_events_status_updated", feishu_events.c.status, feishu_events.c.updated_at)


quality_turns = Table(
    "quality_turns",
    metadata,
    Column("id", Text, primary_key=True),
    Column("run_id", Text, nullable=False, unique=True),
    Column("conversation_id", Text, nullable=False),
    Column("channel", Text, nullable=False),
    Column("question", Text, nullable=False),
    Column("answer", Text),
    Column("knowledge_space_id", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("provider", Text, nullable=False),
    Column("model_name", Text, nullable=False),
    Column("last_agent", Text, nullable=False, server_default=""),
    Column("application_version", Text, nullable=False),
    Column("prompt_version", Text, nullable=False),
    Column("feedback_token_hash", Text, nullable=False),
    Column("routed_domains_json", JSONB, nullable=False),
    Column("specialists_used_json", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


quality_feedback = Table(
    "quality_feedback",
    metadata,
    Column("id", Text, primary_key=True),
    Column(
        "turn_id",
        Text,
        ForeignKey("quality_turns.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("channel", Text, nullable=False),
    Column("feedback_key", Text, nullable=False),
    Column("user_id", Text),
    Column("user_name", Text),
    Column("rating", Text, nullable=False),
    Column("reason", Text, nullable=False, server_default=""),
    Column("reason_code", Text, nullable=False, server_default=""),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "turn_id",
        "channel",
        "feedback_key",
        name="uq_quality_feedback_turn_channel_key",
    ),
)
Index("ix_quality_feedback_rating", quality_feedback.c.rating)


vector_entries = Table(
    "vector_entries",
    metadata,
    Column("collection_name", Text, primary_key=True),
    Column("id", Text, primary_key=True),
    Column("content", Text, nullable=False),
    Column("heading", Text, nullable=False, server_default=""),
    Column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("embedding", VECTOR(1024), nullable=False),
    Column("content_hash", Text),
    Column("app_id", Text),
    Column("domain", Text),
    Column("source_id", Text),
    Column("source_type", Text),
    Column("branch", Text),
    Column("owner_id", Text),
    Column("scope_type", Text),
    Column("space_id", Text),
    Column("enabled", Boolean, nullable=False, server_default=text("true")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)


vector_shadow_comparisons = Table(
    "vector_shadow_comparisons",
    metadata,
    Column("id", Text, primary_key=True),
    Column("primary_ids", JSONB, nullable=False),
    Column("shadow_ids", JSONB, nullable=False),
    Column("primary_latency_ms", Float, nullable=False),
    Column("shadow_latency_ms", Float, nullable=False),
    Column("top_k_overlap", Float, nullable=False),
    Column("status", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
Index(
    "ix_vector_entries_scope",
    vector_entries.c.collection_name,
    vector_entries.c.app_id,
    vector_entries.c.domain,
    vector_entries.c.branch,
    vector_entries.c.source_type,
    vector_entries.c.enabled,
)
Index(
    "ix_vector_entries_source",
    vector_entries.c.collection_name,
    vector_entries.c.source_id,
)
Index(
    "ix_vector_entries_memory_scope",
    vector_entries.c.collection_name,
    vector_entries.c.owner_id,
    vector_entries.c.scope_type,
    vector_entries.c.space_id,
    vector_entries.c.domain,
)
Index(
    "ix_vector_entries_metadata",
    vector_entries.c.metadata,
    postgresql_using="gin",
)


# Register every migrated business table in the shared Core metadata. Domain
# repositories still own their full column contracts during the transition;
# Alembic remains the DDL authority until those definitions are centralized.
from knowledge.migrations.sqlite_postgres import PRIMARY_KEYS as _PRIMARY_KEYS

for _table_name, _primary_key_columns in _PRIMARY_KEYS.items():
    if _table_name in metadata.tables:
        continue
    Table(
        _table_name,
        metadata,
        *(
            Column(_column_name, Text, primary_key=True)
            for _column_name in _primary_key_columns
        ),
    )
