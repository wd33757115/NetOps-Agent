"""add rule generation jobs

Revision ID: 0022_rule_generation_jobs
Revises: 0021_device_tombstones
Create Date: 2026-07-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0022_rule_generation_jobs"
down_revision: Union[str, Sequence[str], None] = "0021_device_tombstones"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "netops_rule_generation_jobs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("job_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source_ref", sa.String(length=512), nullable=True),
        sa.Column("asset_ref", sa.String(length=512), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("validation_report_json", sa.JSON(), nullable=True),
        sa.Column("approval_required", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("job_type", "status", "source_ref", "asset_ref"):
        op.create_index(
            f"ix_netops_rule_generation_jobs_{column}",
            "netops_rule_generation_jobs",
            [column],
        )
    op.create_index(
        "ix_rule_generation_jobs_type_status",
        "netops_rule_generation_jobs",
        ["job_type", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_rule_generation_jobs_type_status",
        table_name="netops_rule_generation_jobs",
    )
    for column in ("asset_ref", "source_ref", "status", "job_type"):
        op.drop_index(
            f"ix_netops_rule_generation_jobs_{column}",
            table_name="netops_rule_generation_jobs",
        )
    op.drop_table("netops_rule_generation_jobs")
