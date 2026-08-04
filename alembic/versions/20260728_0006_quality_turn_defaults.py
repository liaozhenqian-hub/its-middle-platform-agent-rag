"""Align quality turn defaults with the SQLite repository contract."""

from alembic import op
import sqlalchemy as sa


revision = "20260728_0006"
down_revision = "20260728_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "quality_turns",
        "last_agent",
        existing_type=sa.Text(),
        server_default=sa.text("''"),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "quality_turns",
        "last_agent",
        existing_type=sa.Text(),
        server_default=None,
        existing_nullable=False,
    )
