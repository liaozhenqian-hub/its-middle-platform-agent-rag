"""Create long-term memory tables."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260728_0004"
down_revision = "20260728_0003"
branch_labels = None
depends_on = None

J = postgresql.JSONB
T = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "conversation_memory_summaries",
        sa.Column("conversation_id", sa.Text(), primary_key=True), sa.Column("user_id", sa.Text(), nullable=False), sa.Column("space_id", sa.Text(), nullable=False), sa.Column("domain_id", sa.Text()), sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("goals_json", J(), nullable=False, server_default=sa.text("'[]'::jsonb")), sa.Column("confirmed_facts_json", J(), nullable=False, server_default=sa.text("'[]'::jsonb")), sa.Column("unresolved_items_json", J(), nullable=False, server_default=sa.text("'[]'::jsonb")), sa.Column("preferences_json", J(), nullable=False, server_default=sa.text("'[]'::jsonb")), sa.Column("created_at", T, nullable=False), sa.Column("updated_at", T, nullable=False),
    )
    op.create_table(
        "memory_candidates",
        sa.Column("id", sa.Text(), primary_key=True), sa.Column("scope_type", sa.Text(), nullable=False), sa.Column("owner_id", sa.Text(), nullable=False), sa.Column("space_id", sa.Text(), nullable=False), sa.Column("domain_id", sa.Text()), sa.Column("memory_type", sa.Text(), nullable=False), sa.Column("subject", sa.Text(), nullable=False), sa.Column("normalized_fact", sa.Text(), nullable=False), sa.Column("summary", sa.Text(), nullable=False), sa.Column("source_turn_id", sa.Text()), sa.Column("source_citations_json", J(), nullable=False, server_default=sa.text("'[]'::jsonb")), sa.Column("confidence", sa.Float(), nullable=False), sa.Column("status", sa.Text(), nullable=False), sa.Column("expires_at", T), sa.Column("created_at", T, nullable=False), sa.Column("updated_at", T, nullable=False), sa.Column("review_state", sa.Text(), nullable=False, server_default="pending"), sa.Column("review_reason", sa.Text()), sa.Column("legacy_format", sa.Text()),
    )
    op.create_index("ix_memory_candidates_scope_status", "memory_candidates", ["owner_id", "scope_type", "space_id", "domain_id", "status"])
    op.create_table(
        "memories",
        sa.Column("id", sa.Text(), primary_key=True), sa.Column("scope_type", sa.Text(), nullable=False), sa.Column("owner_id", sa.Text(), nullable=False), sa.Column("space_id", sa.Text(), nullable=False), sa.Column("domain_id", sa.Text()), sa.Column("memory_type", sa.Text(), nullable=False), sa.Column("subject", sa.Text(), nullable=False), sa.Column("normalized_fact", sa.Text(), nullable=False), sa.Column("summary", sa.Text(), nullable=False), sa.Column("source_turn_id", sa.Text()), sa.Column("source_citations_json", J(), nullable=False, server_default=sa.text("'[]'::jsonb")), sa.Column("confidence", sa.Float(), nullable=False), sa.Column("status", sa.Text(), nullable=False), sa.Column("valid_from", T, nullable=False), sa.Column("valid_until", T), sa.Column("last_used_at", T), sa.Column("supersedes_id", sa.Text()), sa.Column("created_at", T, nullable=False), sa.Column("updated_at", T, nullable=False), sa.Column("review_state", sa.Text(), nullable=False, server_default="approved"), sa.Column("review_reason", sa.Text()), sa.Column("legacy_format", sa.Text()),
    )
    op.create_index("ix_memories_isolation", "memories", ["owner_id", "scope_type", "space_id", "domain_id", "status"])
    op.create_table(
        "memory_extraction_jobs",
        sa.Column("id", sa.Text(), primary_key=True), sa.Column("user_id", sa.Text(), nullable=False), sa.Column("conversation_id", sa.Text(), nullable=False), sa.Column("space_id", sa.Text(), nullable=False), sa.Column("domain_id", sa.Text()), sa.Column("channel", sa.Text(), nullable=False), sa.Column("question", sa.Text(), nullable=False), sa.Column("answer", sa.Text()), sa.Column("source_turn_id", sa.Text()), sa.Column("source_citations_json", J(), nullable=False, server_default=sa.text("'[]'::jsonb")), sa.Column("status", sa.Text(), nullable=False), sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"), sa.Column("worker_id", sa.Text()), sa.Column("error_type", sa.Text()), sa.Column("created_at", T, nullable=False), sa.Column("updated_at", T, nullable=False),
    )
    op.create_index("ix_memory_extraction_claim", "memory_extraction_jobs", ["status", "created_at"])
    op.create_table("memory_conflicts", sa.Column("id", sa.Text(), primary_key=True), sa.Column("memory_id", sa.Text(), nullable=False), sa.Column("reason_code", sa.Text(), nullable=False), sa.Column("resolved", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("created_at", T, nullable=False), sa.Column("resolved_at", T))
    op.create_table("memory_index_repairs", sa.Column("memory_id", sa.Text(), primary_key=True), sa.Column("operation", sa.Text(), nullable=False), sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"), sa.Column("last_error_type", sa.Text()), sa.Column("created_at", T, nullable=False), sa.Column("updated_at", T, nullable=False))
    op.create_table("memory_audit_events", sa.Column("id", sa.Text(), primary_key=True), sa.Column("memory_id", sa.Text()), sa.Column("candidate_id", sa.Text()), sa.Column("actor", sa.Text(), nullable=False), sa.Column("action", sa.Text(), nullable=False), sa.Column("details_json", J(), nullable=False, server_default=sa.text("'{}'::jsonb")), sa.Column("created_at", T, nullable=False))
    op.create_table(
        "memory_procedural_specs",
        sa.Column("record_id", sa.Text(), primary_key=True), sa.Column("task_type", sa.Text(), nullable=False), sa.Column("procedure_version", sa.Integer(), nullable=False),
        *[sa.Column(name, J(), nullable=False) for name in ("trigger_conditions_json", "required_inputs_json", "environment_constraints_json", "branch_constraints_json", "steps_json", "allowed_tools_json", "stop_conditions_json", "fallback_actions_json", "expected_output_json", "validation_steps_json")],
        sa.Column("minimum_evidence_grade", sa.Text(), nullable=False), sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"), sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"), sa.Column("last_executed_at", T), sa.Column("reviewed_by", sa.Text()), sa.Column("reviewed_at", T), sa.Column("created_at", T, nullable=False), sa.Column("updated_at", T, nullable=False),
    )
    op.create_table("memory_domain_promotions", sa.Column("id", sa.Text(), primary_key=True), sa.Column("source_memory_id", sa.Text(), nullable=False), sa.Column("target_candidate_id", sa.Text()), sa.Column("target_domain_id", sa.Text(), nullable=False), sa.Column("public_summary", sa.Text(), nullable=False), sa.Column("state", sa.Text(), nullable=False), sa.Column("requested_by", sa.Text(), nullable=False), sa.Column("reviewed_by", sa.Text()), sa.Column("reviewed_at", T), sa.Column("valid_until", T), sa.Column("created_at", T, nullable=False), sa.Column("updated_at", T, nullable=False))
    op.create_table("memory_entities", sa.Column("id", sa.Text(), primary_key=True), sa.Column("scope_type", sa.Text(), nullable=False), sa.Column("owner_id", sa.Text(), nullable=False), sa.Column("space_id", sa.Text(), nullable=False), sa.Column("domain_id", sa.Text()), sa.Column("entity_type", sa.Text(), nullable=False), sa.Column("canonical_name", sa.Text(), nullable=False), sa.Column("normalized_name", sa.Text(), nullable=False), sa.Column("branch", sa.Text()), sa.Column("environment", sa.Text()), sa.Column("status", sa.Text(), nullable=False), sa.Column("created_at", T, nullable=False), sa.Column("updated_at", T, nullable=False), sa.UniqueConstraint("scope_type", "owner_id", "space_id", "entity_type", "normalized_name", "branch", "environment", name="uq_memory_entity_identity"))
    op.create_index("ix_memory_entities_isolation", "memory_entities", ["owner_id", "scope_type", "space_id", "domain_id", "status"])
    op.create_table("memory_entity_aliases", sa.Column("entity_id", sa.Text(), sa.ForeignKey("memory_entities.id", ondelete="CASCADE"), primary_key=True), sa.Column("alias", sa.Text(), nullable=False), sa.Column("normalized_alias", sa.Text(), primary_key=True), sa.Column("created_at", T, nullable=False))
    op.create_table("memory_entity_relations", sa.Column("id", sa.Text(), primary_key=True), sa.Column("source_entity_id", sa.Text(), sa.ForeignKey("memory_entities.id", ondelete="CASCADE"), nullable=False), sa.Column("target_entity_id", sa.Text(), sa.ForeignKey("memory_entities.id", ondelete="CASCADE"), nullable=False), sa.Column("relation_type", sa.Text(), nullable=False), sa.Column("summary", sa.Text(), nullable=False), sa.Column("confidence", sa.Float(), nullable=False), sa.Column("status", sa.Text(), nullable=False), sa.Column("created_at", T, nullable=False), sa.Column("updated_at", T, nullable=False), sa.UniqueConstraint("source_entity_id", "target_entity_id", "relation_type", name="uq_memory_entity_relation"))
    op.create_table("memory_entity_evidence", sa.Column("relation_id", sa.Text(), sa.ForeignKey("memory_entity_relations.id", ondelete="CASCADE"), primary_key=True), sa.Column("source_type", sa.Text(), primary_key=True), sa.Column("source_id", sa.Text(), primary_key=True), sa.Column("created_at", T, nullable=False))


def downgrade() -> None:
    for table in ("memory_entity_evidence", "memory_entity_relations", "memory_entity_aliases", "memory_entities", "memory_domain_promotions", "memory_procedural_specs", "memory_audit_events", "memory_index_repairs", "memory_conflicts", "memory_extraction_jobs", "memories", "memory_candidates", "conversation_memory_summaries"):
        op.drop_table(table)
