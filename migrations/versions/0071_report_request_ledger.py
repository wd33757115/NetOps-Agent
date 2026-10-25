"""Add durable report materialization request idempotency.

Revision ID: 0071_report_request_ledger
Revises: 0070_rollout_live_capture
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0071_report_request_ledger"
down_revision = "0070_rollout_live_capture"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "netops_patrol_report_requests",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("patrol_batch_id", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="queued",
        ),
        sa.Column("requested_by", sa.String(length=256), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("report_manifest_id", sa.String(length=64), nullable=True),
        sa.Column("report_manifest_digest", sa.String(length=64), nullable=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'generating', 'completed', 'failed')",
            name="ck_patrol_report_request_status",
        ),
        sa.ForeignKeyConstraint(
            ["patrol_batch_id"],
            ["netops_patrol_batches.patrol_batch_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id"),
    )
    for column in (
        "patrol_batch_id",
        "status",
        "report_manifest_id",
        "report_manifest_digest",
    ):
        op.create_index(
            f"ix_netops_patrol_report_requests_{column}",
            "netops_patrol_report_requests",
            [column],
        )
    op.create_index(
        "uq_patrol_report_request_batch_key",
        "netops_patrol_report_requests",
        ["patrol_batch_id", "idempotency_key"],
        unique=True,
    )
    op.create_index(
        "ix_patrol_report_request_batch_status",
        "netops_patrol_report_requests",
        ["patrol_batch_id", "status"],
    )
    op.add_column(
        "netops_patrol_batches",
        sa.Column("report_request_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_netops_patrol_batches_report_request_id",
        "netops_patrol_batches",
        ["report_request_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_netops_patrol_batches_report_request_id",
        table_name="netops_patrol_batches",
    )
    op.drop_column("netops_patrol_batches", "report_request_id")
    op.drop_index(
        "ix_patrol_report_request_batch_status",
        table_name="netops_patrol_report_requests",
    )
    op.drop_index(
        "uq_patrol_report_request_batch_key",
        table_name="netops_patrol_report_requests",
    )
    for column in (
        "report_manifest_digest",
        "report_manifest_id",
        "status",
        "patrol_batch_id",
    ):
        op.drop_index(
            f"ix_netops_patrol_report_requests_{column}",
            table_name="netops_patrol_report_requests",
        )
    op.drop_table("netops_patrol_report_requests")
