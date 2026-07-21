"""Separate offline patrol jobs from rule asset source jobs.

Revision ID: 0048_offline_job_purpose
Revises: 0047_rule_asset_command_units
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0048_offline_job_purpose"
down_revision = "0047_rule_asset_command_units"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "netops_offline_patrol_jobs",
        sa.Column(
            "purpose",
            sa.String(length=32),
            nullable=False,
            server_default="offline_patrol",
        ),
    )
    op.create_index(
        "ix_offline_patrol_jobs_purpose_created",
        "netops_offline_patrol_jobs",
        ["purpose", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_offline_patrol_jobs_purpose_created",
        table_name="netops_offline_patrol_jobs",
    )
    op.drop_column("netops_offline_patrol_jobs", "purpose")
