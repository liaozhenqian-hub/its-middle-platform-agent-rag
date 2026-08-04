"""Create user authentication and conversation ownership tables."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260728_0002"
down_revision = "20260728_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "anonymous_devices",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("owner_id", sa.Text(), nullable=False, unique=True),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("disabled_at", sa.DateTime(timezone=True)),
        sa.Column("merged_to_open_id", sa.Text()),
    )
    op.create_index("ix_anonymous_devices_expiry", "anonymous_devices", ["expires_at", "disabled_at"])
    op.create_table(
        "feishu_users",
        sa.Column("open_id", sa.Text(), primary_key=True),
        sa.Column("tenant_key", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("avatar_url", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "oauth_login_states",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("state_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("anonymous_owner_id", sa.Text(), sa.ForeignKey("anonymous_devices.owner_id")),
        sa.Column("redirect_path", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_oauth_states_expiry", "oauth_login_states", ["expires_at", "consumed_at"])
    op.create_table(
        "user_sessions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("open_id", sa.Text(), sa.ForeignKey("feishu_users.open_id"), nullable=False),
        sa.Column("csrf_token", sa.Text(), nullable=False),
        sa.Column("source_anonymous_owner_id", sa.Text(), sa.ForeignKey("anonymous_devices.owner_id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sliding_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_user_sessions_expiry", "user_sessions", ["sliding_expires_at", "absolute_expires_at", "revoked_at"])
    op.create_table(
        "personal_api_tokens",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("open_id", sa.Text(), sa.ForeignKey("feishu_users.open_id"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("display_prefix", sa.Text(), nullable=False),
        sa.Column("scopes_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("open_id", "name", name="uq_personal_api_tokens_owner_name"),
    )
    op.create_table(
        "web_conversation_owners",
        sa.Column("conversation_id", sa.Text(), primary_key=True),
        sa.Column("owner_id", sa.Text(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("title", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_conversation_owner", "web_conversation_owners", ["owner_id", "last_seen_at"])
    op.create_table(
        "identity_merge_jobs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("source_anonymous_owner_id", sa.Text(), nullable=False),
        sa.Column("target_open_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("result_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("error_type", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_anonymous_owner_id", "target_open_id", name="uq_identity_merge_source_target"),
    )
    op.create_table(
        "auth_audit_events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("actor_id", sa.Text()),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("subject_type", sa.Text(), nullable=False),
        sa.Column("subject_id", sa.Text()),
        sa.Column("details_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("auth_audit_events")
    op.drop_table("identity_merge_jobs")
    op.drop_index("ix_conversation_owner", table_name="web_conversation_owners")
    op.drop_table("web_conversation_owners")
    op.drop_table("personal_api_tokens")
    op.drop_index("ix_user_sessions_expiry", table_name="user_sessions")
    op.drop_table("user_sessions")
    op.drop_index("ix_oauth_states_expiry", table_name="oauth_login_states")
    op.drop_table("oauth_login_states")
    op.drop_table("feishu_users")
    op.drop_index("ix_anonymous_devices_expiry", table_name="anonymous_devices")
    op.drop_table("anonymous_devices")
