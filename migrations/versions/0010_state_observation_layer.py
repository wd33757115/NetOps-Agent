"""add state observation and current state layer

Revision ID: 0010_state_observation
Revises: 0009_pg_parser_samples
Create Date: 2026-06-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0010_state_observation"
down_revision: Union[str, Sequence[str], None] = "0009_pg_parser_samples"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "netops_state_observations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("snapshot_id", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.String(length=256), nullable=True),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("device_id", sa.String(length=256), nullable=False),
        sa.Column("vendor", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("canonical_command", sa.String(length=512), nullable=False),
        sa.Column("entity_type", sa.String(length=128), nullable=False),
        sa.Column("entity_key", sa.String(length=1024), nullable=False),
        sa.Column("field_name", sa.String(length=256), nullable=False),
        sa.Column("value_json", sa.JSON(), nullable=True),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("value_number", sa.Float(), nullable=True),
        sa.Column("value_type", sa.String(length=32), nullable=False),
        sa.Column("observation_kind", sa.String(length=32), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("parser_template_id", sa.String(length=64), nullable=True),
        sa.Column("parser_version", sa.String(length=64), nullable=True),
        sa.Column("contract_version", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["netops_command_snapshots.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "run_id",
        "snapshot_id",
        "dataset_id",
        "mode",
        "source",
        "device_id",
        "canonical_command",
        "entity_type",
        "field_name",
        "observation_kind",
        "observed_at",
        "parser_template_id",
    ):
        op.create_index(
            f"ix_netops_state_observations_{column}",
            "netops_state_observations",
            [column],
        )
    op.create_index(
        "uq_netops_state_observation_idempotency",
        "netops_state_observations",
        ["idempotency_key"],
        unique=True,
    )
    op.create_index(
        "ix_netops_state_observation_identity",
        "netops_state_observations",
        ["device_id", "entity_type", "field_name", "observed_at"],
    )
    op.create_index(
        "ix_netops_state_observation_snapshot_entity",
        "netops_state_observations",
        ["snapshot_id", "entity_type", "entity_key"],
    )

    op.create_table(
        "netops_current_state",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("state_key", sa.String(length=64), nullable=False),
        sa.Column("device_id", sa.String(length=256), nullable=False),
        sa.Column("vendor", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("entity_type", sa.String(length=128), nullable=False),
        sa.Column("entity_key", sa.String(length=1024), nullable=False),
        sa.Column("field_name", sa.String(length=256), nullable=False),
        sa.Column("value_json", sa.JSON(), nullable=True),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("value_number", sa.Float(), nullable=True),
        sa.Column("value_type", sa.String(length=32), nullable=False),
        sa.Column("observation_kind", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_run_id", sa.String(length=128), nullable=True),
        sa.Column("source_snapshot_id", sa.String(length=64), nullable=True),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["source_snapshot_id"],
            ["netops_command_snapshots.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "device_id",
        "entity_type",
        "field_name",
        "observation_kind",
        "source",
        "source_run_id",
        "source_snapshot_id",
        "observed_at",
    ):
        op.create_index(
            f"ix_netops_current_state_{column}",
            "netops_current_state",
            [column],
        )
    op.create_index(
        "uq_netops_current_state_key",
        "netops_current_state",
        ["state_key"],
        unique=True,
    )
    op.create_index(
        "ix_netops_current_state_identity",
        "netops_current_state",
        ["device_id", "entity_type", "field_name"],
    )

    op.add_column(
        "netops_network_changes",
        sa.Column("previous_observation_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "netops_network_changes",
        sa.Column("current_observation_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "netops_network_changes",
        sa.Column(
            "source",
            sa.String(length=64),
            nullable=False,
            server_default="patrol",
        ),
    )
    op.add_column(
        "netops_network_changes",
        sa.Column("old_number", sa.Float(), nullable=True),
    )
    op.add_column(
        "netops_network_changes",
        sa.Column("new_number", sa.Float(), nullable=True),
    )
    op.add_column(
        "netops_network_changes",
        sa.Column(
            "event_status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
    )
    for column in (
        "previous_observation_id",
        "current_observation_id",
        "source",
        "event_status",
    ):
        op.create_index(
            f"ix_netops_network_changes_{column}",
            "netops_network_changes",
            [column],
        )


def downgrade() -> None:
    for column in (
        "event_status",
        "source",
        "current_observation_id",
        "previous_observation_id",
    ):
        op.drop_index(
            f"ix_netops_network_changes_{column}",
            table_name="netops_network_changes",
        )
    for column in (
        "event_status",
        "new_number",
        "old_number",
        "source",
        "current_observation_id",
        "previous_observation_id",
    ):
        op.drop_column("netops_network_changes", column)
    op.drop_table("netops_current_state")
    op.drop_table("netops_state_observations")
