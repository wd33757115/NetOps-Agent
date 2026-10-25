"""add patrol analysis ownership fencing

Revision ID: 0030_patrol_ownership_fencing
Revises: 0029_patrol_runtime_concurrency
Create Date: 2026-07-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030_patrol_ownership_fencing"
down_revision: str | Sequence[str] | None = "0029_patrol_runtime_concurrency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    timestamp_columns = {
        "netops_patrol_runs": (
            "heartbeat_at",
            "lease_until",
            "last_progress_at",
            "started_at",
            "completed_at",
            "created_at",
            "updated_at",
        ),
        "netops_patrol_analysis_attempts": (
            "heartbeat_at",
            "lease_until",
            "started_at",
            "completed_at",
            "created_at",
            "updated_at",
        ),
        "netops_network_events": (
            "occurred_at",
            "first_seen_at",
            "last_seen_at",
            "created_at",
        ),
        "netops_network_event_occurrences": ("observed_at", "created_at"),
    }
    for table_name, columns in timestamp_columns.items():
        for column_name in columns:
            op.alter_column(
                table_name,
                column_name,
                existing_type=sa.DateTime(),
                type_=sa.DateTime(timezone=True),
                postgresql_using=(
                    f"{column_name} AT TIME ZONE current_setting('TIMEZONE')"
                ),
                existing_nullable=column_name
                in {"heartbeat_at", "lease_until", "last_progress_at", "completed_at"},
            )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM netops_change_policy_versions
                WHERE status = 'active'
                GROUP BY observation_type
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION
                    'multiple active ChangePolicies exist for one observation_type';
            END IF;
        END $$
        """
    )
    op.create_index(
        "uq_change_policy_active_observation_type",
        "netops_change_policy_versions",
        ["observation_type"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.add_column(
        "netops_audit_logs",
        sa.Column("idempotency_key", sa.String(256), nullable=True),
    )
    op.create_index(
        "uq_netops_audit_logs_idempotency_key",
        "netops_audit_logs",
        ["idempotency_key"],
        unique=True,
    )
    op.add_column(
        "netops_notifications",
        sa.Column("idempotency_key", sa.String(256), nullable=True),
    )
    op.create_index(
        "uq_netops_notifications_idempotency_key",
        "netops_notifications",
        ["idempotency_key"],
        unique=True,
    )
    op.add_column(
        "netops_network_changes",
        sa.Column(
            "analysis_revision",
            sa.String(128),
            nullable=False,
            server_default="original",
        ),
    )
    op.create_index(
        "ix_netops_network_changes_analysis_revision",
        "netops_network_changes",
        ["analysis_revision"],
    )
    op.add_column(
        "netops_network_changes",
        sa.Column("rule_bundle_digest", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_netops_network_changes_rule_bundle_digest",
        "netops_network_changes",
        ["rule_bundle_digest"],
    )
    op.add_column(
        "netops_patrol_analysis_attempts",
        sa.Column("owner_token", sa.String(64), nullable=True),
    )
    op.execute(
        """
        UPDATE netops_patrol_analysis_attempts
        SET owner_token = md5(concat_ws('|', id, attempt_number::text, updated_at::text))
        WHERE owner_token IS NULL
        """
    )
    op.alter_column(
        "netops_patrol_analysis_attempts",
        "owner_token",
        existing_type=sa.String(64),
        nullable=False,
    )
    op.create_index(
        "ix_netops_patrol_analysis_attempts_owner_token",
        "netops_patrol_analysis_attempts",
        ["owner_token"],
    )
    op.add_column(
        "netops_patrol_analysis_attempts",
        sa.Column("result_digest", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_netops_patrol_analysis_attempts_result_digest",
        "netops_patrol_analysis_attempts",
        ["result_digest"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_netops_network_changes_rule_bundle_digest",
        table_name="netops_network_changes",
    )
    op.drop_column("netops_network_changes", "rule_bundle_digest")
    op.drop_index(
        "ix_netops_network_changes_analysis_revision",
        table_name="netops_network_changes",
    )
    op.drop_column("netops_network_changes", "analysis_revision")
    op.drop_index(
        "uq_netops_notifications_idempotency_key",
        table_name="netops_notifications",
    )
    op.drop_column("netops_notifications", "idempotency_key")
    op.drop_index(
        "uq_netops_audit_logs_idempotency_key",
        table_name="netops_audit_logs",
    )
    op.drop_column("netops_audit_logs", "idempotency_key")
    op.drop_index(
        "uq_change_policy_active_observation_type",
        table_name="netops_change_policy_versions",
    )
    op.drop_index(
        "ix_netops_patrol_analysis_attempts_result_digest",
        table_name="netops_patrol_analysis_attempts",
    )
    op.drop_column("netops_patrol_analysis_attempts", "result_digest")
    op.drop_index(
        "ix_netops_patrol_analysis_attempts_owner_token",
        table_name="netops_patrol_analysis_attempts",
    )
    op.drop_column("netops_patrol_analysis_attempts", "owner_token")
    timestamp_columns = {
        "netops_patrol_runs": (
            "heartbeat_at",
            "lease_until",
            "last_progress_at",
            "started_at",
            "completed_at",
            "created_at",
            "updated_at",
        ),
        "netops_patrol_analysis_attempts": (
            "heartbeat_at",
            "lease_until",
            "started_at",
            "completed_at",
            "created_at",
            "updated_at",
        ),
        "netops_network_events": (
            "occurred_at",
            "first_seen_at",
            "last_seen_at",
            "created_at",
        ),
        "netops_network_event_occurrences": ("observed_at", "created_at"),
    }
    for table_name, columns in timestamp_columns.items():
        for column_name in columns:
            op.alter_column(
                table_name,
                column_name,
                existing_type=sa.DateTime(timezone=True),
                type_=sa.DateTime(),
                postgresql_using=(
                    f"{column_name} AT TIME ZONE current_setting('TIMEZONE')"
                ),
                existing_nullable=column_name
                in {"heartbeat_at", "lease_until", "last_progress_at", "completed_at"},
            )
