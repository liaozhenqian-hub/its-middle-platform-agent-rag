"""Create knowledge catalog and source worker tables."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260728_0003"
down_revision = "20260728_0002"
branch_labels = None
depends_on = None


def _timestamps():
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def upgrade() -> None:
    op.create_table("knowledge_spaces", sa.Column("id", sa.Text(), primary_key=True), sa.Column("name", sa.Text(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("knowledge_domains", sa.Column("id", sa.Text(), primary_key=True), sa.Column("space_id", sa.Text(), sa.ForeignKey("knowledge_spaces.id"), nullable=False), sa.Column("name", sa.Text(), nullable=False), sa.Column("sort_order", sa.Integer(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.execute("INSERT INTO knowledge_spaces(id,name,created_at) VALUES ('middle-platform','中台',TIMESTAMPTZ '2026-01-01T00:00:00+00:00')")
    op.execute("INSERT INTO knowledge_domains(id,space_id,name,sort_order,created_at) VALUES ('metric-platform','middle-platform','指标平台',10,TIMESTAMPTZ '2026-01-01T00:00:00+00:00'),('approval-flow','middle-platform','审批流',20,TIMESTAMPTZ '2026-01-01T00:00:00+00:00'),('workflow','middle-platform','工作流',30,TIMESTAMPTZ '2026-01-01T00:00:00+00:00')")
    op.create_table(
        "knowledge_sources",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("space_id", sa.Text(), sa.ForeignKey("knowledge_spaces.id"), nullable=False),
        sa.Column("domain_id", sa.Text(), sa.ForeignKey("knowledge_domains.id")),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("config_json", postgresql.JSONB(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_timestamps(),
    )
    op.create_index("ix_knowledge_sources_scope", "knowledge_sources", ["space_id", "domain_id", "source_type", "enabled"])
    op.create_table(
        "source_domain_rules",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("source_id", sa.Text(), sa.ForeignKey("knowledge_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("pattern", sa.Text(), nullable=False),
        sa.Column("target_domain_id", sa.Text(), sa.ForeignKey("knowledge_domains.id")),
        sa.Column("shared", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "source_versions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("source_id", sa.Text(), sa.ForeignKey("knowledge_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_ref", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("current", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False),
        *_timestamps(),
    )
    op.create_index("ix_source_versions_source_current", "source_versions", ["source_id", "current"])
    op.create_table(
        "source_files",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("source_id", sa.Text(), sa.ForeignKey("knowledge_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_id", sa.Text(), sa.ForeignKey("source_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("domain_key", sa.Text(), nullable=False),
        sa.Column("language", sa.Text()),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_source_files_source_version", "source_files", ["source_id", "version_id", "relative_path"])
    op.create_table(
        "code_symbols",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("source_file_id", sa.Text(), sa.ForeignKey("source_files.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol_type", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("qualified_name", sa.Text()),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.Column("parent_symbol_id", sa.Text()),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_code_symbols_file_name", "code_symbols", ["source_file_id", "name"])
    op.create_table(
        "chunk_catalog",
        sa.Column("chunk_id", sa.Text(), primary_key=True),
        sa.Column("source_id", sa.Text(), sa.ForeignKey("knowledge_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_id", sa.Text(), sa.ForeignKey("source_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_file_id", sa.Text(), sa.ForeignKey("source_files.id", ondelete="CASCADE")),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("domain_key", sa.Text(), nullable=False),
        sa.Column("locator", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False),
        *_timestamps(),
    )
    op.create_index("ix_chunk_catalog_source_version", "chunk_catalog", ["source_id", "version_id", "domain_key"])
    op.create_table(
        "sync_jobs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("source_id", sa.Text(), sa.ForeignKey("knowledge_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("target_commit", sa.Text()),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text()),
        sa.Column("worker_id", sa.Text()),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        *_timestamps(),
    )
    op.create_index("ix_sync_jobs_claim", "sync_jobs", ["state", "available_at", "created_at"])
    op.create_table("swagger_cache", sa.Column("source_id", sa.Text(), sa.ForeignKey("knowledge_sources.id", ondelete="CASCADE"), primary_key=True), sa.Column("specification_json", postgresql.JSONB(), nullable=False), sa.Column("etag", sa.Text()), sa.Column("last_modified", sa.Text()), sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("encrypted_secrets", sa.Column("source_id", sa.Text(), sa.ForeignKey("knowledge_sources.id", ondelete="CASCADE"), primary_key=True), sa.Column("secret_kind", sa.Text(), primary_key=True), sa.Column("encrypted_value", sa.Text(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("source_webhook_secrets", sa.Column("source_id", sa.Text(), sa.ForeignKey("knowledge_sources.id", ondelete="CASCADE"), primary_key=True), sa.Column("secret_hash", sa.Text(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("admin_sessions", sa.Column("id", sa.Text(), primary_key=True), sa.Column("token_hash", sa.Text(), nullable=False, unique=True), sa.Column("username", sa.Text(), nullable=False), sa.Column("csrf_token", sa.Text(), nullable=False), sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("audit_events", sa.Column("id", sa.Text(), primary_key=True), sa.Column("actor", sa.Text(), nullable=False), sa.Column("action", sa.Text(), nullable=False), sa.Column("resource_type", sa.Text(), nullable=False), sa.Column("resource_id", sa.Text()), sa.Column("details_json", postgresql.JSONB(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))


def downgrade() -> None:
    for table in ("audit_events", "admin_sessions", "source_webhook_secrets", "encrypted_secrets", "swagger_cache", "sync_jobs", "chunk_catalog", "code_symbols", "source_files", "source_versions", "source_domain_rules", "knowledge_sources", "knowledge_domains", "knowledge_spaces"):
        op.drop_table(table)
