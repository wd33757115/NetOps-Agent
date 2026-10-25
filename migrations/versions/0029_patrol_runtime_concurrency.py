"""enforce patrol runtime concurrency and occurrence invariants

Revision ID: 0029_patrol_runtime_concurrency
Revises: 0028_patrol_replay_integrity
Create Date: 2026-07-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029_patrol_runtime_concurrency"
down_revision: str | Sequence[str] | None = "0028_patrol_replay_integrity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE netops_network_event_occurrences AS occurrence
        SET occurrence_kind = 'opening'
        FROM netops_network_events AS event
        WHERE occurrence.event_id = event.id
          AND occurrence.run_id IS NOT DISTINCT FROM event.run_id
          AND occurrence.source_change_id IS NOT DISTINCT FROM event.source_change_id
          AND occurrence.observed_at = event.occurred_at
          AND occurrence.occurrence_kind = 'observed'
        """
    )
    op.execute(
        """
        INSERT INTO netops_network_event_occurrences (
            id, idempotency_key, event_id, run_id, source_change_id,
            observed_at, severity, occurrence_kind, payload_json, created_at
        )
        SELECT
            'opening-' || md5(event.id),
            md5(concat_ws('|', 'opening-v2', event.id, event.run_id,
                event.source_change_id, event.occurred_at::text)),
            event.id,
            event.run_id,
            event.source_change_id,
            event.occurred_at,
            event.severity,
            'opening',
            event.payload_json,
            COALESCE(event.created_at, event.occurred_at)
        FROM netops_network_events AS event
        WHERE NOT EXISTS (
            SELECT 1
            FROM netops_network_event_occurrences AS occurrence
            WHERE occurrence.event_id = event.id
              AND occurrence.occurrence_kind = 'opening'
        )
        """
    )
    op.execute(
        """
        UPDATE netops_network_event_outbox AS outbox
        SET occurrence_id = (
            SELECT occurrence.id
            FROM netops_network_event_occurrences AS occurrence
            WHERE occurrence.event_id = outbox.event_id
            ORDER BY
                CASE WHEN occurrence.occurrence_kind = 'opening' THEN 0 ELSE 1 END,
                occurrence.observed_at,
                occurrence.created_at,
                occurrence.id
            LIMIT 1
        )
        WHERE outbox.occurrence_id IS NULL
        """
    )
    op.alter_column(
        "netops_network_event_outbox",
        "occurrence_id",
        existing_type=sa.String(64),
        nullable=False,
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM netops_network_events
                WHERE status IN ('open', 'acknowledged', 'recovering')
                  AND correlation_key <> ''
                GROUP BY event_type, correlation_key
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION
                    'duplicate active network events exist; resolve them before migration 0029';
            END IF;
        END $$
        """
    )
    op.create_index(
        "uq_netops_network_events_active_correlation",
        "netops_network_events",
        ["event_type", "correlation_key"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('open', 'acknowledged', 'recovering') AND correlation_key <> ''"
        ),
    )

    op.execute(
        """
        UPDATE netops_patrol_runs
        SET logical_run_id = id
        WHERE logical_run_id IS NULL OR logical_run_id = ''
        """
    )
    op.alter_column(
        "netops_patrol_runs",
        "logical_run_id",
        existing_type=sa.String(128),
        nullable=False,
    )
    op.create_index(
        "uq_netops_patrol_runs_logical_attempt",
        "netops_patrol_runs",
        ["logical_run_id", "attempt_number"],
        unique=True,
    )
    op.create_table(
        "netops_patrol_analysis_attempts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(128),
            sa.ForeignKey("netops_patrol_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stage", sa.String(64), nullable=False),
        sa.Column("scope_hash", sa.String(64), nullable=False),
        sa.Column("scope_json", sa.JSON(), nullable=False),
        sa.Column("rule_bundle_digest", sa.String(64), nullable=False),
        sa.Column("rule_bundle_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=False),
        sa.Column("lease_until", sa.DateTime(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "uq_netops_patrol_analysis_scope",
        "netops_patrol_analysis_attempts",
        ["run_id", "stage", "scope_hash"],
        unique=True,
    )
    op.create_index(
        "ix_netops_patrol_analysis_attempts_run_id",
        "netops_patrol_analysis_attempts",
        ["run_id"],
    )
    op.create_index(
        "ix_netops_patrol_analysis_attempts_stage",
        "netops_patrol_analysis_attempts",
        ["stage"],
    )
    op.create_index(
        "ix_netops_patrol_analysis_attempts_status",
        "netops_patrol_analysis_attempts",
        ["status"],
    )
    op.create_index(
        "ix_netops_patrol_analysis_attempts_lease_until",
        "netops_patrol_analysis_attempts",
        ["lease_until"],
    )
    op.create_index(
        "ix_netops_patrol_analysis_attempts_rule_bundle_digest",
        "netops_patrol_analysis_attempts",
        ["rule_bundle_digest"],
    )
    op.add_column(
        "netops_command_snapshots",
        sa.Column("execution_item_id", sa.String(128), nullable=True),
    )
    op.execute(
        """
        UPDATE netops_command_snapshots
        SET execution_item_id = md5(concat_ws('|', 'snapshot-item-v1', run_id,
            device_id, canonical_command, COALESCE(start_line, 0)::text))
        WHERE execution_item_id IS NULL
        """
    )
    op.alter_column(
        "netops_command_snapshots",
        "execution_item_id",
        existing_type=sa.String(128),
        nullable=False,
    )
    op.create_index(
        "ix_netops_command_snapshots_execution_item_id",
        "netops_command_snapshots",
        ["execution_item_id"],
    )

    op.add_column(
        "netops_patrol_collection_results",
        sa.Column("execution_item_id", sa.String(128), nullable=True),
    )
    op.execute(
        """
        UPDATE netops_patrol_collection_results
        SET execution_item_id = md5(concat_ws('|', 'collection-result-v1', run_id,
            device_id, canonical_command))
        WHERE execution_item_id IS NULL
        """
    )
    op.alter_column(
        "netops_patrol_collection_results",
        "execution_item_id",
        existing_type=sa.String(128),
        nullable=False,
    )
    op.drop_index(
        "uq_netops_patrol_collection_result",
        table_name="netops_patrol_collection_results",
    )
    op.create_index(
        "uq_netops_patrol_collection_result",
        "netops_patrol_collection_results",
        ["run_id", "device_id", "execution_item_id"],
        unique=True,
    )
    op.create_index(
        "ix_netops_patrol_collection_results_execution_item_id",
        "netops_patrol_collection_results",
        ["execution_item_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_netops_patrol_collection_results_execution_item_id",
        table_name="netops_patrol_collection_results",
    )
    op.drop_index(
        "uq_netops_patrol_collection_result",
        table_name="netops_patrol_collection_results",
    )
    op.create_index(
        "uq_netops_patrol_collection_result",
        "netops_patrol_collection_results",
        ["run_id", "device_id", "canonical_command"],
        unique=True,
    )
    op.drop_column("netops_patrol_collection_results", "execution_item_id")
    op.drop_index(
        "ix_netops_command_snapshots_execution_item_id",
        table_name="netops_command_snapshots",
    )
    op.drop_column("netops_command_snapshots", "execution_item_id")
    op.drop_table("netops_patrol_analysis_attempts")
    op.drop_index(
        "uq_netops_patrol_runs_logical_attempt",
        table_name="netops_patrol_runs",
    )
    op.alter_column(
        "netops_patrol_runs",
        "logical_run_id",
        existing_type=sa.String(128),
        nullable=True,
    )
    op.drop_index(
        "uq_netops_network_events_active_correlation",
        table_name="netops_network_events",
    )
    op.alter_column(
        "netops_network_event_outbox",
        "occurrence_id",
        existing_type=sa.String(64),
        nullable=True,
    )
