"""Create persistence foundation tables."""

from alembic import op
from pgvector.sqlalchemy import VECTOR
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260728_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "storage_migration_runs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("migration_type", sa.Text(), nullable=False),
        sa.Column("source_fingerprint", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("summary", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "storage_migration_steps",
        sa.Column("run_id", sa.Text(), sa.ForeignKey("storage_migration_runs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("step_name", sa.Text(), primary_key=True),
        sa.Column("cursor", sa.Text()),
        sa.Column("processed_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("summary", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "agent_sessions",
        sa.Column("session_id", sa.Text(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "agent_messages",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column("session_id", sa.Text(), sa.ForeignKey("agent_sessions.session_id", ondelete="CASCADE"), nullable=False),
        sa.Column("message_data", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_agent_messages_session_order", "agent_messages", ["session_id", "id"])
    op.create_table(
        "agent_pending_runs",
        sa.Column("run_id", sa.Text(), primary_key=True),
        sa.Column("conversation_id", sa.Text(), nullable=False),
        sa.Column("state", postgresql.JSONB(), nullable=False),
        sa.Column("approvals", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_agent_pending_runs_conversation_status",
        "agent_pending_runs",
        ["conversation_id", "status"],
    )
    op.create_table(
        "agent_conversation_scopes",
        sa.Column("conversation_id", sa.Text(), primary_key=True),
        sa.Column("knowledge_space_id", sa.Text(), nullable=False),
        sa.Column("domain_id", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "feishu_events",
        sa.Column("event_id", sa.Text(), primary_key=True),
        sa.Column("message_id", sa.Text(), nullable=False),
        sa.Column("chat_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("error_type", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('processing','completed','failed')", name="ck_feishu_events_status"),
        sa.CheckConstraint("attempt BETWEEN 1 AND 2", name="ck_feishu_events_attempt"),
    )
    op.create_index("ix_feishu_events_status_updated", "feishu_events", ["status", "updated_at"])
    op.create_table(
        "vector_entries",
        sa.Column("collection_name", sa.Text(), primary_key=True),
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("heading", sa.Text(), nullable=False, server_default=""),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("embedding", VECTOR(1024), nullable=False),
        sa.Column("content_hash", sa.Text()),
        sa.Column("app_id", sa.Text()),
        sa.Column("domain", sa.Text()),
        sa.Column("source_id", sa.Text()),
        sa.Column("source_type", sa.Text()),
        sa.Column("branch", sa.Text()),
        sa.Column("owner_id", sa.Text()),
        sa.Column("scope_type", sa.Text()),
        sa.Column("space_id", sa.Text()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_vector_entries_scope",
        "vector_entries",
        ["collection_name", "app_id", "domain", "branch", "source_type", "enabled"],
    )
    op.create_index(
        "ix_vector_entries_source",
        "vector_entries",
        ["collection_name", "source_id"],
    )
    op.create_index(
        "ix_vector_entries_memory_scope",
        "vector_entries",
        ["collection_name", "owner_id", "scope_type", "space_id", "domain"],
    )
    op.create_index(
        "ix_vector_entries_metadata",
        "vector_entries",
        ["metadata"],
        postgresql_using="gin",
    )
    op.create_table(
        "vector_shadow_comparisons",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("primary_ids", postgresql.JSONB(), nullable=False),
        sa.Column("shadow_ids", postgresql.JSONB(), nullable=False),
        sa.Column("primary_latency_ms", sa.Float(), nullable=False),
        sa.Column("shadow_latency_ms", sa.Float(), nullable=False),
        sa.Column("top_k_overlap", sa.Float(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("vector_shadow_comparisons")
    op.drop_index("ix_vector_entries_metadata", table_name="vector_entries")
    op.drop_index("ix_vector_entries_memory_scope", table_name="vector_entries")
    op.drop_index("ix_vector_entries_source", table_name="vector_entries")
    op.drop_index("ix_vector_entries_scope", table_name="vector_entries")
    op.drop_table("vector_entries")
    op.drop_index("ix_feishu_events_status_updated", table_name="feishu_events")
    op.drop_table("feishu_events")
    op.drop_table("agent_conversation_scopes")
    op.drop_index("ix_agent_pending_runs_conversation_status", table_name="agent_pending_runs")
    op.drop_table("agent_pending_runs")
    op.drop_index("ix_agent_messages_session_order", table_name="agent_messages")
    op.drop_table("agent_messages")
    op.drop_table("agent_sessions")
    op.drop_table("storage_migration_steps")
    op.drop_table("storage_migration_runs")
