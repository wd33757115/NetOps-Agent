"""Add persistent rollout campaigns and stage attempts.

Revision ID: 0068_rollout_campaign
Revises: 0067_report_purpose_raw_evidence
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0068_rollout_campaign"
down_revision = "0067_report_purpose_raw_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "netops_rollout_campaigns",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column(
            "idempotency_key",
            sa.String(length=128),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("current_stage", sa.String(length=32), nullable=True),
        sa.Column(
            "paused_from_stage",
            sa.String(length=32),
            nullable=True,
        ),
        sa.Column("target_manifest_json", sa.JSON(), nullable=True),
        sa.Column(
            "target_manifest_digest",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("runtime_bundle_refs_json", sa.JSON(), nullable=True),
        sa.Column(
            "runtime_bundle_digest",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "comparison_context_ids_json",
            sa.JSON(),
            nullable=True,
        ),
        sa.Column(
            "comparison_context_digest",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("baseline_window_json", sa.JSON(), nullable=True),
        sa.Column("thresholds_json", sa.JSON(), nullable=True),
        sa.Column("rollback_plan_json", sa.JSON(), nullable=True),
        sa.Column("pause_context_json", sa.JSON(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "lock_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "created_by",
            sa.String(length=128),
            nullable=False,
        ),
        sa.Column("frozen_by", sa.String(length=128), nullable=True),
        sa.Column("paused_by", sa.String(length=128), nullable=True),
        sa.Column("completed_by", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "frozen_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "paused_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "rolled_back_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "status IN ("
            "'draft', 'target_frozen', 'shadow', 'canary_5', "
            "'canary_20', 'canary_50', 'full_100', 'observing', "
            "'paused', 'rolled_back', 'completed'"
            ")",
            name="ck_rollout_campaign_status",
        ),
        sa.CheckConstraint(
            "current_stage IS NULL OR current_stage IN ("
            "'shadow', 'canary_5', 'canary_20', 'canary_50', "
            "'full_100', 'observing'"
            ")",
            name="ck_rollout_campaign_current_stage",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    for column in (
        "status",
        "current_stage",
        "target_manifest_digest",
        "runtime_bundle_digest",
        "comparison_context_digest",
        "created_by",
        "frozen_by",
    ):
        op.create_index(
            f"ix_netops_rollout_campaigns_{column}",
            "netops_rollout_campaigns",
            [column],
        )
    op.create_index(
        "ix_rollout_campaign_status_updated",
        "netops_rollout_campaigns",
        ["status", "updated_at"],
    )

    op.create_table(
        "netops_rollout_stage_attempts",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column(
            "campaign_id",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("percentage", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "target_manifest_digest",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "runtime_bundle_digest",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "comparison_context_digest",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("baseline_window_json", sa.JSON(), nullable=False),
        sa.Column("thresholds_json", sa.JSON(), nullable=False),
        sa.Column("metrics_json", sa.JSON(), nullable=True),
        sa.Column("comparison_result_json", sa.JSON(), nullable=True),
        sa.Column("evidence_refs_json", sa.JSON(), nullable=True),
        sa.Column("blockers_json", sa.JSON(), nullable=True),
        sa.Column(
            "decision_digest",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("started_by", sa.String(length=128), nullable=True),
        sa.Column("approved_by", sa.String(length=128), nullable=True),
        sa.Column("approval_comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "approved_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "stage IN ("
            "'shadow', 'canary_5', 'canary_20', 'canary_50', "
            "'full_100', 'observing'"
            ")",
            name="ck_rollout_stage_attempt_stage",
        ),
        sa.CheckConstraint(
            "status IN ("
            "'pending', 'running', 'awaiting_approval', 'passed', "
            "'failed', 'paused', 'rolled_back'"
            ")",
            name="ck_rollout_stage_attempt_status",
        ),
        sa.CheckConstraint(
            "percentage IN (0, 5, 20, 50, 100)",
            name="ck_rollout_stage_attempt_percentage",
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["netops_rollout_campaigns.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "campaign_id",
        "stage",
        "status",
        "decision_digest",
        "started_by",
        "approved_by",
    ):
        op.create_index(
            f"ix_netops_rollout_stage_attempts_{column}",
            "netops_rollout_stage_attempts",
            [column],
        )
    op.create_index(
        "uq_rollout_stage_attempt",
        "netops_rollout_stage_attempts",
        ["campaign_id", "stage", "attempt_number"],
        unique=True,
    )
    op.create_index(
        "ix_rollout_stage_attempt_campaign_status",
        "netops_rollout_stage_attempts",
        ["campaign_id", "status"],
    )


def downgrade() -> None:
    op.drop_table("netops_rollout_stage_attempts")
    op.drop_table("netops_rollout_campaigns")
