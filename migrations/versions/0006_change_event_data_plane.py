"""add PostgreSQL change and event data plane

Revision ID: 0006_change_event_data_plane
Revises: 0005_patrol_fingerprint_v2
Create Date: 2026-06-15
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006_change_event_data_plane"
down_revision: Union[str, Sequence[str], None] = "0005_patrol_fingerprint_v2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "netops_network_changes",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=True),
        sa.Column("device_id", sa.String(length=256), nullable=True),
        sa.Column("canonical_command", sa.String(length=512), nullable=True),
        sa.Column("entity_type", sa.String(length=128), nullable=False),
        sa.Column("entity_key", sa.String(length=1024), nullable=False),
        sa.Column("field", sa.String(length=256), nullable=False),
        sa.Column("old_value_json", sa.JSON(), nullable=True),
        sa.Column("new_value_json", sa.JSON(), nullable=True),
        sa.Column("change_type", sa.String(length=32), nullable=False),
        sa.Column("previous_snapshot_id", sa.String(length=64), nullable=True),
        sa.Column("current_snapshot_id", sa.String(length=64), nullable=True),
        sa.Column("detected_at", sa.DateTime(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    for column in (
        "idempotency_key",
        "run_id",
        "device_id",
        "canonical_command",
        "entity_type",
        "field",
        "change_type",
        "previous_snapshot_id",
        "current_snapshot_id",
        "detected_at",
    ):
        op.create_index(
            f"ix_netops_network_changes_{column}",
            "netops_network_changes",
            [column],
            unique=False,
        )

    op.create_table(
        "netops_network_events",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("device_id", sa.String(length=256), nullable=True),
        sa.Column("entity_type", sa.String(length=128), nullable=False),
        sa.Column("entity_key", sa.String(length=1024), nullable=False),
        sa.Column("source_change_id", sa.String(length=64), nullable=True),
        sa.Column("run_id", sa.String(length=128), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    for column in (
        "idempotency_key",
        "event_type",
        "severity",
        "device_id",
        "entity_type",
        "source_change_id",
        "run_id",
        "occurred_at",
        "status",
    ):
        op.create_index(
            f"ix_netops_network_events_{column}",
            "netops_network_events",
            [column],
            unique=False,
        )


def downgrade() -> None:
    op.drop_table("netops_network_events")
    op.drop_table("netops_network_changes")
