"""add patrol failure semantics and distributed outbox state

Revision ID: 0026_patrol_failure_semantics
Revises: 0025_patrol_schema_alignment
Create Date: 2026-07-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0026_patrol_failure_semantics"
down_revision: Union[str, Sequence[str], None] = "0025_patrol_schema_alignment"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "netops_network_events",
        sa.Column("correlation_key", sa.String(1024), nullable=True),
    )
    op.execute(
        "UPDATE netops_network_events SET correlation_key = COALESCE("
        "payload_json->>'correlation_key', "
        "regexp_replace(COALESCE(payload_json->>'dedupe_key', ''), '^[^|]*[|]', '')"
        ")"
    )
    op.alter_column("netops_network_events", "correlation_key", nullable=False)
    op.create_index(
        "ix_netops_network_events_correlation_key",
        "netops_network_events",
        ["correlation_key"],
    )
    op.create_index(
        "ix_netops_network_events_active_correlation",
        "netops_network_events",
        ["event_type", "correlation_key", "status"],
    )

    for column in (
        sa.Column("claim_token", sa.String(64), nullable=True),
        sa.Column("lease_until", sa.DateTime(), nullable=True),
        sa.Column("dead_letter_at", sa.DateTime(), nullable=True),
    ):
        op.add_column("netops_network_event_outbox", column)
    op.create_index(
        "ix_netops_network_event_outbox_claim_token",
        "netops_network_event_outbox",
        ["claim_token"],
    )
    op.create_index(
        "ix_netops_network_event_outbox_lease_until",
        "netops_network_event_outbox",
        ["lease_until"],
    )

    op.create_table(
        "netops_network_change_decisions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("device_id", sa.String(256), nullable=True),
        sa.Column("canonical_command", sa.String(512), nullable=True),
        sa.Column("entity_type", sa.String(128), nullable=False),
        sa.Column("entity_key", sa.String(1024), nullable=False),
        sa.Column("field", sa.String(256), nullable=False),
        sa.Column("observation_type", sa.String(256), nullable=True),
        sa.Column("previous_observation_id", sa.String(64), nullable=True),
        sa.Column("current_observation_id", sa.String(64), nullable=True),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(256), nullable=True),
        sa.Column("change_policy_id", sa.String(64), nullable=True),
        sa.Column("change_policy_version_id", sa.String(64), nullable=True),
        sa.Column("comparison_type", sa.String(64), nullable=True),
        sa.Column("old_value_json", sa.JSON(), nullable=True),
        sa.Column("new_value_json", sa.JSON(), nullable=True),
        sa.Column("observed_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    for column in (
        "run_id", "device_id", "canonical_command", "entity_type", "field",
        "observation_type", "previous_observation_id", "current_observation_id",
        "decision", "change_policy_id", "change_policy_version_id",
        "observed_at", "expires_at",
    ):
        op.create_index(
            f"ix_netops_network_change_decisions_{column}",
            "netops_network_change_decisions",
            [column],
        )
    op.create_index(
        "ix_netops_change_decisions_idempotency",
        "netops_network_change_decisions",
        ["idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("netops_network_change_decisions")
    op.drop_index(
        "ix_netops_network_event_outbox_lease_until",
        table_name="netops_network_event_outbox",
    )
    op.drop_index(
        "ix_netops_network_event_outbox_claim_token",
        table_name="netops_network_event_outbox",
    )
    for column in ("dead_letter_at", "lease_until", "claim_token"):
        op.drop_column("netops_network_event_outbox", column)
    op.drop_index(
        "ix_netops_network_events_active_correlation",
        table_name="netops_network_events",
    )
    op.drop_index(
        "ix_netops_network_events_correlation_key",
        table_name="netops_network_events",
    )
    op.drop_column("netops_network_events", "correlation_key")
