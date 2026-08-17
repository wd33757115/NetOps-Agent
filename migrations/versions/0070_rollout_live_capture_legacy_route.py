"""Add live-capture evidence and governed legacy rollout routes.

Revision ID: 0070_rollout_live_capture
Revises: 0069_rollout_stage_execution
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0070_rollout_live_capture"
down_revision = "0069_rollout_stage_execution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "netops_rollout_outbox_aggregate_id_key",
        "netops_rollout_outbox",
        type_="unique",
    )

    op.create_table(
        "netops_rollout_legacy_routes",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("route_name", sa.String(length=256), nullable=False),
        sa.Column("check_item_version_id", sa.String(length=64), nullable=False),
        sa.Column("implementation_version_id", sa.String(length=64), nullable=False),
        sa.Column("adapter_kind", sa.String(length=64), nullable=False),
        sa.Column("adapter_version", sa.String(length=32), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="active",
        ),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deprecated_by", sa.String(length=128), nullable=True),
        sa.Column("deprecated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deprecation_reason", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'deprecated')",
            name="ck_rollout_legacy_route_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("digest"),
        sa.UniqueConstraint("idempotency_key"),
    )
    for column in (
        "check_item_version_id",
        "implementation_version_id",
        "adapter_kind",
        "digest",
        "status",
        "created_by",
    ):
        op.create_index(
            f"ix_netops_rollout_legacy_routes_{column}",
            "netops_rollout_legacy_routes",
            [column],
        )
    op.create_index(
        "ix_rollout_legacy_route_scope_status",
        "netops_rollout_legacy_routes",
        [
            "check_item_version_id",
            "implementation_version_id",
            "status",
        ],
    )

    op.add_column(
        "netops_rollout_stage_executions",
        sa.Column("capture_batch_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "netops_rollout_stage_executions",
        sa.Column("capture_manifest_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "netops_rollout_stage_executions",
        sa.Column(
            "capture_manifest_digest",
            sa.String(length=64),
            nullable=True,
        ),
    )
    op.add_column(
        "netops_rollout_stage_executions",
        sa.Column(
            "capture_requested_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "netops_rollout_stage_executions",
        sa.Column(
            "capture_completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_netops_rollout_stage_executions_capture_batch_id",
        "netops_rollout_stage_executions",
        ["capture_batch_id"],
    )
    op.create_index(
        "ix_netops_rollout_stage_executions_capture_manifest_digest",
        "netops_rollout_stage_executions",
        ["capture_manifest_digest"],
    )
    op.drop_constraint(
        "ck_rollout_stage_execution_status",
        "netops_rollout_stage_executions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_rollout_stage_execution_status",
        "netops_rollout_stage_executions",
        "status IN ("
        "'queued', 'capturing', 'running', 'evaluating', 'completed', "
        "'failed', 'cancelled'"
        ")",
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM netops_rollout_outbox newer "
        "USING netops_rollout_outbox older "
        "WHERE newer.aggregate_id = older.aggregate_id "
        "AND (newer.created_at, newer.id) > (older.created_at, older.id)"
    )
    op.create_unique_constraint(
        "netops_rollout_outbox_aggregate_id_key",
        "netops_rollout_outbox",
        ["aggregate_id"],
    )
    op.execute(
        "UPDATE netops_rollout_stage_executions "
        "SET status = 'failed', failure_code = 'live_capture_downgraded' "
        "WHERE status = 'capturing'"
    )
    op.drop_constraint(
        "ck_rollout_stage_execution_status",
        "netops_rollout_stage_executions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_rollout_stage_execution_status",
        "netops_rollout_stage_executions",
        "status IN ("
        "'queued', 'running', 'evaluating', 'completed', "
        "'failed', 'cancelled'"
        ")",
    )
    op.drop_index(
        "ix_netops_rollout_stage_executions_capture_manifest_digest",
        table_name="netops_rollout_stage_executions",
    )
    op.drop_index(
        "ix_netops_rollout_stage_executions_capture_batch_id",
        table_name="netops_rollout_stage_executions",
    )
    for column in (
        "capture_completed_at",
        "capture_requested_at",
        "capture_manifest_digest",
        "capture_manifest_json",
        "capture_batch_id",
    ):
        op.drop_column("netops_rollout_stage_executions", column)

    op.drop_index(
        "ix_rollout_legacy_route_scope_status",
        table_name="netops_rollout_legacy_routes",
    )
    for column in (
        "created_by",
        "status",
        "digest",
        "adapter_kind",
        "implementation_version_id",
        "check_item_version_id",
    ):
        op.drop_index(
            f"ix_netops_rollout_legacy_routes_{column}",
            table_name="netops_rollout_legacy_routes",
        )
    op.drop_table("netops_rollout_legacy_routes")
