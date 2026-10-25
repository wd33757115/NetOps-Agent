"""Add materialized patrol batch summary projection.

Revision ID: 0037_batch_summary_projection
Revises: 0036_device_readiness_projection
"""

import sqlalchemy as sa
from alembic import op

revision = "0037_batch_summary_projection"
down_revision = "0036_device_readiness_projection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "netops_patrol_batch_summary_projections",
        sa.Column("patrol_batch_id", sa.String(128), primary_key=True),
        sa.Column("source_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("run_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("markdown_text", sa.Text(), nullable=False),
        sa.Column("object_key", sa.String(512), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_patrol_batch_summary_source_digest",
        "netops_patrol_batch_summary_projections",
        ["source_digest"],
    )
    op.create_index(
        "ix_patrol_batch_summary_status",
        "netops_patrol_batch_summary_projections",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_patrol_batch_summary_status",
        table_name="netops_patrol_batch_summary_projections",
    )
    op.drop_index(
        "ix_patrol_batch_summary_source_digest",
        table_name="netops_patrol_batch_summary_projections",
    )
    op.drop_table("netops_patrol_batch_summary_projections")
