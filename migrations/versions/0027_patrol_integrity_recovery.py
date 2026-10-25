"""add patrol integrity, attempts, event provenance, and occurrences

Revision ID: 0027_patrol_integrity_recovery
Revises: 0026_patrol_failure_semantics
Create Date: 2026-07-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_patrol_integrity_recovery"
down_revision: str | Sequence[str] | None = "0026_patrol_failure_semantics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for column in (
        sa.Column("logical_run_id", sa.String(128), nullable=True),
        sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("worker_id", sa.String(256), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=True),
        sa.Column("lease_until", sa.DateTime(), nullable=True),
        sa.Column("last_progress_at", sa.DateTime(), nullable=True),
    ):
        op.add_column("netops_patrol_runs", column)
    for name in (
        "logical_run_id",
        "worker_id",
        "heartbeat_at",
        "lease_until",
        "last_progress_at",
    ):
        op.create_index(f"ix_netops_patrol_runs_{name}", "netops_patrol_runs", [name])
    op.execute("UPDATE netops_patrol_runs SET logical_run_id = id WHERE logical_run_id IS NULL")

    op.add_column(
        "netops_network_changes",
        sa.Column("dimensions_json", sa.JSON(), nullable=True),
    )

    for column in (
        sa.Column("correlation_dimensions_json", sa.JSON(), nullable=True),
        sa.Column("latest_change_id", sa.String(64), nullable=True),
        sa.Column("last_seen_run_id", sa.String(128), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="1"),
    ):
        op.add_column("netops_network_events", column)
    op.execute(
        "UPDATE netops_network_events SET "
        "latest_change_id = source_change_id, last_seen_run_id = run_id, "
        "first_seen_at = occurred_at, last_seen_at = occurred_at, "
        "correlation_dimensions_json = COALESCE("
        "payload_json->'correlation_dimensions', '{}'::json)"
    )
    op.alter_column("netops_network_events", "first_seen_at", nullable=False)
    op.alter_column("netops_network_events", "last_seen_at", nullable=False)
    for name in (
        "latest_change_id",
        "last_seen_run_id",
        "first_seen_at",
        "last_seen_at",
    ):
        op.create_index(f"ix_netops_network_events_{name}", "netops_network_events", [name])

    op.create_table(
        "netops_network_event_occurrences",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column(
            "event_id",
            sa.String(64),
            sa.ForeignKey("netops_network_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("run_id", sa.String(128), nullable=True),
        sa.Column("source_change_id", sa.String(64), nullable=True),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("severity", sa.String(32), nullable=False),
        sa.Column("occurrence_kind", sa.String(32), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    for name in (
        "event_id",
        "run_id",
        "source_change_id",
        "observed_at",
        "severity",
        "occurrence_kind",
    ):
        op.create_index(
            f"ix_netops_network_event_occurrences_{name}",
            "netops_network_event_occurrences",
            [name],
        )
    op.create_index(
        "ix_netops_event_occurrence_idempotency",
        "netops_network_event_occurrences",
        ["idempotency_key"],
        unique=True,
    )
    op.create_index(
        "ix_netops_event_occurrence_run_event",
        "netops_network_event_occurrences",
        ["run_id", "event_id", "observed_at"],
    )


def downgrade() -> None:
    op.drop_table("netops_network_event_occurrences")
    for name in (
        "last_seen_at",
        "first_seen_at",
        "last_seen_run_id",
        "latest_change_id",
    ):
        op.drop_index(f"ix_netops_network_events_{name}", table_name="netops_network_events")
    for name in (
        "occurrence_count",
        "last_seen_at",
        "first_seen_at",
        "last_seen_run_id",
        "latest_change_id",
        "correlation_dimensions_json",
    ):
        op.drop_column("netops_network_events", name)
    op.drop_column("netops_network_changes", "dimensions_json")
    for name in (
        "last_progress_at",
        "lease_until",
        "heartbeat_at",
        "worker_id",
        "logical_run_id",
    ):
        op.drop_index(f"ix_netops_patrol_runs_{name}", table_name="netops_patrol_runs")
    for name in (
        "last_progress_at",
        "lease_until",
        "heartbeat_at",
        "worker_id",
        "attempt_number",
        "logical_run_id",
    ):
        op.drop_column("netops_patrol_runs", name)
