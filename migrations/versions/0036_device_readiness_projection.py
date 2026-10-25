"""Add rebuildable device readiness projection.

Revision ID: 0036_device_readiness_projection
Revises: 0035_output_classification
"""

import sqlalchemy as sa
from alembic import op

revision = "0036_device_readiness_projection"
down_revision = "0035_output_classification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "netops_device_readiness_projections",
        sa.Column("device_id", sa.String(64), nullable=False),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("source_digest", sa.String(64), nullable=False),
        sa.Column("readiness_json", sa.JSON(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["netops_devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("device_id", "mode"),
    )
    op.create_index(
        "ix_device_readiness_projection_status",
        "netops_device_readiness_projections",
        ["status"],
    )
    op.create_index(
        "ix_device_readiness_projection_source_digest",
        "netops_device_readiness_projections",
        ["source_digest"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_device_readiness_projection_source_digest",
        table_name="netops_device_readiness_projections",
    )
    op.drop_index(
        "ix_device_readiness_projection_status",
        table_name="netops_device_readiness_projections",
    )
    op.drop_table("netops_device_readiness_projections")
