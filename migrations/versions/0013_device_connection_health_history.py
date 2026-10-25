"""add device connection health history

Revision ID: 0013_device_conn_health_history
Revises: 0012_device_connection_health
Create Date: 2026-06-21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0013_device_conn_health_history"
down_revision: Union[str, Sequence[str], None] = "0012_device_connection_health"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "netops_device_connection_health",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("failure_code", sa.String(length=128), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("checked_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["device_id"], ["netops_devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("device_id", "status", "failure_code", "source", "checked_at"):
        op.create_index(
            f"ix_netops_device_connection_health_{column}",
            "netops_device_connection_health",
            [column],
        )


def downgrade() -> None:
    op.drop_table("netops_device_connection_health")
