"""add collection execution plans

Revision ID: 0020_collection_execution_plans
Revises: 0019_observation_contracts
Create Date: 2026-06-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0020_collection_execution_plans"
down_revision: Union[str, Sequence[str], None] = "0019_observation_contracts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "netops_collection_execution_plans",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("profile_name", sa.String(length=128), nullable=False),
        sa.Column("profile_version_id", sa.String(length=64), nullable=True),
        sa.Column("binding_id", sa.String(length=64), nullable=True),
        sa.Column("policy_source", sa.String(length=64), nullable=False),
        sa.Column("credential_ref", sa.String(length=512), nullable=True),
        sa.Column("collector_options_json", sa.JSON(), nullable=True),
        sa.Column("selector_json", sa.JSON(), nullable=True),
        sa.Column("plan_json", sa.JSON(), nullable=False),
        sa.Column("command_count", sa.Integer(), nullable=False),
        sa.Column("plan_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("launch_source", sa.String(length=64), nullable=True),
        sa.Column("patrol_batch_id", sa.String(length=128), nullable=True),
        sa.Column("workflow_run_id", sa.String(length=64), nullable=True),
        sa.Column("celery_task_id", sa.String(length=128), nullable=True),
        sa.Column("failure_code", sa.String(length=128), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("launched_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "device_id",
        "action",
        "mode",
        "profile_name",
        "profile_version_id",
        "binding_id",
        "policy_source",
        "plan_sha256",
        "status",
        "launch_source",
        "patrol_batch_id",
        "workflow_run_id",
        "celery_task_id",
    ):
        op.create_index(
            f"ix_netops_collection_execution_plans_{column}",
            "netops_collection_execution_plans",
            [column],
        )
    op.create_index(
        "ix_collection_execution_plan_launch",
        "netops_collection_execution_plans",
        ["patrol_batch_id", "status"],
    )
    op.create_index(
        "ix_collection_execution_plan_device_created",
        "netops_collection_execution_plans",
        ["device_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_collection_execution_plan_device_created",
        table_name="netops_collection_execution_plans",
    )
    op.drop_index(
        "ix_collection_execution_plan_launch",
        table_name="netops_collection_execution_plans",
    )
    for column in (
        "celery_task_id",
        "workflow_run_id",
        "patrol_batch_id",
        "launch_source",
        "status",
        "plan_sha256",
        "policy_source",
        "binding_id",
        "profile_version_id",
        "profile_name",
        "mode",
        "action",
        "device_id",
    ):
        op.drop_index(
            f"ix_netops_collection_execution_plans_{column}",
            table_name="netops_collection_execution_plans",
        )
    op.drop_table("netops_collection_execution_plans")
