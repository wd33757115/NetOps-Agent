"""Add durable patrol batch dispatch state.

Revision ID: 0044_patrol_batch_dispatch
Revises: 0043_parser_job_asset_uniqueness
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0044_patrol_batch_dispatch"
down_revision = "0043_parser_job_asset_uniqueness"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_netops_workflow_step_run_index",
        "netops_workflow_steps",
        ["run_id", "step_index"],
        unique=True,
    )
    op.add_column(
        "netops_patrol_batches",
        sa.Column("save_baseline", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "netops_patrol_batches",
        sa.Column("dispatch_status", sa.String(32), server_default="completed", nullable=False),
    )
    op.add_column(
        "netops_patrol_batches", sa.Column("dispatch_requested_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "netops_patrol_batches", sa.Column("dispatch_started_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "netops_patrol_batches", sa.Column("dispatch_completed_at", sa.DateTime(timezone=True))
    )
    op.add_column("netops_patrol_batches", sa.Column("dispatch_error", sa.Text()))
    op.add_column(
        "netops_patrol_batches",
        sa.Column("report_status", sa.String(32), server_default="not_requested", nullable=False),
    )
    op.add_column(
        "netops_patrol_batches", sa.Column("report_requested_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "netops_patrol_batches", sa.Column("report_completed_at", sa.DateTime(timezone=True))
    )
    op.create_index(
        "ix_netops_patrol_batches_dispatch_status",
        "netops_patrol_batches",
        ["dispatch_status"],
    )
    op.create_index(
        "ix_netops_patrol_batches_report_status",
        "netops_patrol_batches",
        ["report_status"],
    )

    op.add_column(
        "netops_patrol_batch_devices", sa.Column("execution_plan_id", sa.String(64))
    )
    op.add_column(
        "netops_patrol_batch_devices", sa.Column("celery_task_id", sa.String(128))
    )
    op.add_column(
        "netops_patrol_batch_devices",
        sa.Column("dispatch_status", sa.String(32), server_default="completed", nullable=False),
    )
    op.add_column(
        "netops_patrol_batch_devices",
        sa.Column("dispatch_attempts", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "netops_patrol_batch_devices", sa.Column("dispatch_claim_token", sa.String(64))
    )
    op.add_column(
        "netops_patrol_batch_devices",
        sa.Column("dispatch_lease_until", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "netops_patrol_batch_devices", sa.Column("dispatched_at", sa.DateTime(timezone=True))
    )
    op.create_foreign_key(
        "fk_patrol_batch_device_execution_plan",
        "netops_patrol_batch_devices",
        "netops_collection_execution_plans",
        ["execution_plan_id"],
        ["id"],
        ondelete="SET NULL",
    )
    for column in (
        "execution_plan_id",
        "celery_task_id",
        "dispatch_status",
        "dispatch_claim_token",
        "dispatch_lease_until",
    ):
        op.create_index(
            f"ix_netops_patrol_batch_devices_{column}",
            "netops_patrol_batch_devices",
            [column],
        )

    op.alter_column("netops_patrol_batches", "dispatch_status", server_default="pending")
    op.alter_column("netops_patrol_batch_devices", "dispatch_status", server_default="pending")


def downgrade() -> None:
    op.drop_index(
        "uq_netops_workflow_step_run_index", table_name="netops_workflow_steps"
    )
    op.drop_constraint(
        "fk_patrol_batch_device_execution_plan",
        "netops_patrol_batch_devices",
        type_="foreignkey",
    )
    for column in (
        "dispatch_lease_until",
        "dispatch_claim_token",
        "dispatch_status",
        "celery_task_id",
        "execution_plan_id",
    ):
        op.drop_index(
            f"ix_netops_patrol_batch_devices_{column}",
            table_name="netops_patrol_batch_devices",
        )
    for column in (
        "dispatched_at",
        "dispatch_lease_until",
        "dispatch_claim_token",
        "dispatch_attempts",
        "dispatch_status",
        "celery_task_id",
        "execution_plan_id",
    ):
        op.drop_column("netops_patrol_batch_devices", column)

    op.drop_index(
        "ix_netops_patrol_batches_report_status", table_name="netops_patrol_batches"
    )
    op.drop_index(
        "ix_netops_patrol_batches_dispatch_status", table_name="netops_patrol_batches"
    )
    for column in (
        "report_completed_at",
        "report_requested_at",
        "report_status",
        "dispatch_error",
        "dispatch_completed_at",
        "dispatch_started_at",
        "dispatch_requested_at",
        "dispatch_status",
        "save_baseline",
    ):
        op.drop_column("netops_patrol_batches", column)
