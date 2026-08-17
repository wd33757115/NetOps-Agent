"""Add trusted rollout stage execution and comparison evidence.

Revision ID: 0069_rollout_stage_execution
Revises: 0068_rollout_campaign
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0069_rollout_stage_execution"
down_revision = "0068_rollout_campaign"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "netops_rollout_stage_executions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("campaign_id", sa.String(length=64), nullable=False),
        sa.Column("stage_attempt_id", sa.String(length=64), nullable=False),
        sa.Column(
            "execution_mode",
            sa.String(length=32),
            nullable=False,
            server_default="snapshot_replay",
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="queued",
        ),
        sa.Column("selection_manifest_json", sa.JSON(), nullable=False),
        sa.Column(
            "selection_manifest_digest",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("source_snapshot_ids_json", sa.JSON(), nullable=False),
        sa.Column(
            "snapshot_set_digest",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "fact_high_watermark",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "baseline_projection_digest",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "candidate_projection_digest",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("comparison_manifest_id", sa.String(length=64), nullable=True),
        sa.Column("decision_digest", sa.String(length=64), nullable=True),
        sa.Column("worker_task_id", sa.String(length=128), nullable=True),
        sa.Column("worker_owner", sa.String(length=256), nullable=True),
        sa.Column(
            "fencing_token",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "heartbeat_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("failure_code", sa.String(length=128), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=False),
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
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.CheckConstraint(
            "execution_mode IN ('snapshot_replay', 'live_capture')",
            name="ck_rollout_stage_execution_mode",
        ),
        sa.CheckConstraint(
            "status IN ("
            "'queued', 'running', 'evaluating', 'completed', "
            "'failed', 'cancelled'"
            ")",
            name="ck_rollout_stage_execution_status",
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["netops_rollout_campaigns.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["stage_attempt_id"],
            ["netops_rollout_stage_attempts.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stage_attempt_id"),
    )
    for column in (
        "campaign_id",
        "stage_attempt_id",
        "status",
        "selection_manifest_digest",
        "snapshot_set_digest",
        "baseline_projection_digest",
        "candidate_projection_digest",
        "comparison_manifest_id",
        "decision_digest",
        "worker_task_id",
        "worker_owner",
        "heartbeat_at",
        "failure_code",
        "created_by",
    ):
        op.create_index(
            f"ix_netops_rollout_stage_executions_{column}",
            "netops_rollout_stage_executions",
            [column],
        )
    op.create_index(
        "ix_rollout_stage_execution_campaign_status",
        "netops_rollout_stage_executions",
        ["campaign_id", "status"],
    )

    op.create_table(
        "netops_rollout_projections",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("stage_execution_id", sa.String(length=64), nullable=False),
        sa.Column("track", sa.String(length=16), nullable=False),
        sa.Column("target_key", sa.String(length=64), nullable=False),
        sa.Column("route_json", sa.JSON(), nullable=False),
        sa.Column(
            "runtime_bundle_version_id",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("snapshot_ids_json", sa.JSON(), nullable=False),
        sa.Column(
            "snapshot_set_digest",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("facts_json", sa.JSON(), nullable=True),
        sa.Column("facts_digest", sa.String(length=64), nullable=True),
        sa.Column("failure_code", sa.String(length=128), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "track IN ('baseline', 'candidate')",
            name="ck_rollout_projection_track",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed')",
            name="ck_rollout_projection_status",
        ),
        sa.ForeignKeyConstraint(
            ["stage_execution_id"],
            ["netops_rollout_stage_executions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "stage_execution_id",
        "track",
        "target_key",
        "runtime_bundle_version_id",
        "snapshot_set_digest",
        "status",
        "facts_digest",
        "failure_code",
    ):
        op.create_index(
            f"ix_netops_rollout_projections_{column}",
            "netops_rollout_projections",
            [column],
        )
    op.create_index(
        "uq_rollout_projection_target",
        "netops_rollout_projections",
        ["stage_execution_id", "track", "target_key"],
        unique=True,
    )
    op.create_index(
        "ix_rollout_projection_execution_status",
        "netops_rollout_projections",
        ["stage_execution_id", "status"],
    )

    op.create_table(
        "netops_rollout_comparison_evidence",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("stage_execution_id", sa.String(length=64), nullable=False),
        sa.Column("campaign_id", sa.String(length=64), nullable=False),
        sa.Column("stage_attempt_id", sa.String(length=64), nullable=False),
        sa.Column(
            "schema_version",
            sa.String(length=64),
            nullable=False,
            server_default="rollout-comparison-evidence/v1",
        ),
        sa.Column("manifest_json", sa.JSON(), nullable=False),
        sa.Column("manifest_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "artifact_object_key",
            sa.String(length=1024),
            nullable=False,
        ),
        sa.Column("artifact_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "fact_high_watermark",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["stage_execution_id"],
            ["netops_rollout_stage_executions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["netops_rollout_campaigns.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["stage_attempt_id"],
            ["netops_rollout_stage_attempts.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stage_execution_id"),
        sa.UniqueConstraint("manifest_digest"),
        sa.UniqueConstraint("artifact_object_key"),
    )
    for column in (
        "stage_execution_id",
        "campaign_id",
        "stage_attempt_id",
        "manifest_digest",
        "artifact_sha256",
    ):
        op.create_index(
            f"ix_netops_rollout_comparison_evidence_{column}",
            "netops_rollout_comparison_evidence",
            [column],
        )

    op.create_table(
        "netops_rollout_outbox",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("aggregate_id", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "lease_until",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("claim_token", sa.String(length=64), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "status IN ("
            "'pending', 'publishing', 'published', 'retry_wait', "
            "'dead_letter'"
            ")",
            name="ck_rollout_outbox_status",
        ),
        sa.ForeignKeyConstraint(
            ["aggregate_id"],
            ["netops_rollout_stage_executions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("aggregate_id"),
    )
    for column in (
        "event_type",
        "aggregate_id",
        "status",
        "available_at",
        "lease_until",
        "claim_token",
    ):
        op.create_index(
            f"ix_netops_rollout_outbox_{column}",
            "netops_rollout_outbox",
            [column],
        )
    op.create_index(
        "ix_rollout_outbox_delivery",
        "netops_rollout_outbox",
        ["status", "available_at"],
    )


def downgrade() -> None:
    op.drop_table("netops_rollout_outbox")
    op.drop_table("netops_rollout_comparison_evidence")
    op.drop_table("netops_rollout_projections")
    op.drop_table("netops_rollout_stage_executions")
