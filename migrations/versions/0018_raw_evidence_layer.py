"""add raw evidence metadata layer

Revision ID: 0018_raw_evidence
Revises: 0017_agent_module_window
Create Date: 2026-06-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0018_raw_evidence"
down_revision: Union[str, Sequence[str], None] = "0017_agent_module_window"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "netops_raw_evidence",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("evidence_key", sa.String(length=256), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("evidence_type", sa.String(length=128), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=True),
        sa.Column("dataset_id", sa.String(length=256), nullable=True),
        sa.Column("inventory_device_id", sa.String(length=64), nullable=True),
        sa.Column("device_id", sa.String(length=256), nullable=True),
        sa.Column("source_snapshot_id", sa.String(length=64), nullable=True),
        sa.Column("observed_at", sa.DateTime(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(), nullable=True),
        sa.Column("content_ref", sa.String(length=1024), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("retention_policy", sa.String(length=64), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["inventory_device_id"],
            ["netops_devices.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_snapshot_id"],
            ["netops_command_snapshots.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "source_type",
        "evidence_type",
        "run_id",
        "dataset_id",
        "inventory_device_id",
        "device_id",
        "source_snapshot_id",
        "observed_at",
        "content_hash",
    ):
        op.create_index(
            f"ix_netops_raw_evidence_{column}",
            "netops_raw_evidence",
            [column],
        )
    op.create_index(
        "uq_netops_raw_evidence_evidence_key",
        "netops_raw_evidence",
        ["evidence_key"],
        unique=True,
    )
    op.create_index(
        "uq_netops_raw_evidence_snapshot",
        "netops_raw_evidence",
        ["source_snapshot_id"],
        unique=True,
        postgresql_where=sa.text("source_snapshot_id IS NOT NULL"),
    )
    op.create_index(
        "ix_netops_raw_evidence_lookup",
        "netops_raw_evidence",
        ["source_type", "evidence_type", "device_id", "observed_at"],
    )

    op.add_column(
        "netops_state_observations",
        sa.Column(
            "source_type",
            sa.String(length=64),
            nullable=False,
            server_default="cli",
        ),
    )
    op.add_column(
        "netops_state_observations",
        sa.Column("evidence_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "netops_state_observations",
        sa.Column("schema_id", sa.String(length=128), nullable=True),
    )
    op.create_foreign_key(
        "fk_netops_state_observations_evidence_id",
        "netops_state_observations",
        "netops_raw_evidence",
        ["evidence_id"],
        ["id"],
        ondelete="SET NULL",
    )
    for column in ("source_type", "evidence_id", "schema_id"):
        op.create_index(
            f"ix_netops_state_observations_{column}",
            "netops_state_observations",
            [column],
        )

    op.add_column(
        "netops_current_state",
        sa.Column(
            "source_type",
            sa.String(length=64),
            nullable=False,
            server_default="cli",
        ),
    )
    op.add_column(
        "netops_current_state",
        sa.Column("source_evidence_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "netops_current_state",
        sa.Column("schema_id", sa.String(length=128), nullable=True),
    )
    op.create_foreign_key(
        "fk_netops_current_state_source_evidence_id",
        "netops_current_state",
        "netops_raw_evidence",
        ["source_evidence_id"],
        ["id"],
        ondelete="SET NULL",
    )
    for column in ("source_type", "source_evidence_id", "schema_id"):
        op.create_index(
            f"ix_netops_current_state_{column}",
            "netops_current_state",
            [column],
        )


def downgrade() -> None:
    for column in ("schema_id", "source_evidence_id", "source_type"):
        op.drop_index(
            f"ix_netops_current_state_{column}",
            table_name="netops_current_state",
        )
    op.drop_constraint(
        "fk_netops_current_state_source_evidence_id",
        "netops_current_state",
        type_="foreignkey",
    )
    op.drop_column("netops_current_state", "schema_id")
    op.drop_column("netops_current_state", "source_evidence_id")
    op.drop_column("netops_current_state", "source_type")

    for column in ("schema_id", "evidence_id", "source_type"):
        op.drop_index(
            f"ix_netops_state_observations_{column}",
            table_name="netops_state_observations",
        )
    op.drop_constraint(
        "fk_netops_state_observations_evidence_id",
        "netops_state_observations",
        type_="foreignkey",
    )
    op.drop_column("netops_state_observations", "schema_id")
    op.drop_column("netops_state_observations", "evidence_id")
    op.drop_column("netops_state_observations", "source_type")

    op.drop_index(
        "ix_netops_raw_evidence_lookup",
        table_name="netops_raw_evidence",
    )
    op.drop_index(
        "uq_netops_raw_evidence_snapshot",
        table_name="netops_raw_evidence",
    )
    op.drop_index(
        "uq_netops_raw_evidence_evidence_key",
        table_name="netops_raw_evidence",
    )
    for column in (
        "content_hash",
        "observed_at",
        "source_snapshot_id",
        "device_id",
        "inventory_device_id",
        "dataset_id",
        "run_id",
        "evidence_type",
        "source_type",
    ):
        op.drop_index(
            f"ix_netops_raw_evidence_{column}",
            table_name="netops_raw_evidence",
        )
    op.drop_table("netops_raw_evidence")
