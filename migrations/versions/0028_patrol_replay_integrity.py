"""backfill opening occurrences and publish every event occurrence

Revision ID: 0028_patrol_replay_integrity
Revises: 0027_patrol_integrity_recovery
Create Date: 2026-07-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028_patrol_replay_integrity"
down_revision: str | Sequence[str] | None = "0027_patrol_integrity_recovery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO netops_network_event_occurrences (
            id, idempotency_key, event_id, run_id, source_change_id,
            observed_at, severity, occurrence_kind, payload_json, created_at
        )
        SELECT
            'opening-' || md5(event.id),
            md5(concat_ws('|', 'opening', event.id, event.run_id,
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
        )
        """
    )

    op.add_column(
        "netops_network_event_outbox",
        sa.Column(
            "occurrence_id",
            sa.String(64),
            sa.ForeignKey(
                "netops_network_event_occurrences.id",
                ondelete="CASCADE",
            ),
            nullable=True,
        ),
    )
    op.execute(
        """
        UPDATE netops_network_event_outbox AS outbox
        SET occurrence_id = (
            SELECT occurrence.id
            FROM netops_network_event_occurrences AS occurrence
            WHERE occurrence.event_id = outbox.event_id
            ORDER BY occurrence.observed_at, occurrence.created_at, occurrence.id
            LIMIT 1
        )
        WHERE outbox.occurrence_id IS NULL
        """
    )
    op.drop_index(
        "ix_netops_network_event_outbox_event_id",
        table_name="netops_network_event_outbox",
    )
    op.create_index(
        "ix_netops_network_event_outbox_event_id",
        "netops_network_event_outbox",
        ["event_id"],
    )
    op.create_index(
        "ix_netops_network_event_outbox_occurrence_id",
        "netops_network_event_outbox",
        ["occurrence_id"],
    )
    op.create_index(
        "uq_netops_network_event_outbox_occurrence_id",
        "netops_network_event_outbox",
        ["occurrence_id"],
        unique=True,
        postgresql_where=sa.text("occurrence_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM netops_network_event_outbox AS duplicate
        USING netops_network_event_outbox AS keeper
        WHERE duplicate.event_id = keeper.event_id
          AND (
              duplicate.created_at > keeper.created_at
              OR (
                  duplicate.created_at = keeper.created_at
                  AND duplicate.id > keeper.id
              )
          )
        """
    )
    op.drop_index(
        "uq_netops_network_event_outbox_occurrence_id",
        table_name="netops_network_event_outbox",
    )
    op.drop_index(
        "ix_netops_network_event_outbox_occurrence_id",
        table_name="netops_network_event_outbox",
    )
    op.drop_index(
        "ix_netops_network_event_outbox_event_id",
        table_name="netops_network_event_outbox",
    )
    op.create_index(
        "ix_netops_network_event_outbox_event_id",
        "netops_network_event_outbox",
        ["event_id"],
        unique=True,
    )
    op.drop_column("netops_network_event_outbox", "occurrence_id")
    op.execute(
        """
        DELETE FROM netops_network_event_occurrences
        WHERE occurrence_kind = 'opening'
          AND id LIKE 'opening-%'
        """
    )
