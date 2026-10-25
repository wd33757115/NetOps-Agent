"""add device connection health fields

Revision ID: 0012_device_connection_health
Revises: 0011_rules_profiles
Create Date: 2026-06-21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0012_device_connection_health"
down_revision: Union[str, Sequence[str], None] = "0011_rules_profiles"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "netops_devices",
        sa.Column("last_connection_status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "netops_devices",
        sa.Column("last_connection_checked_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "netops_devices",
        sa.Column("last_connection_failure_code", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "netops_devices",
        sa.Column("last_connection_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "netops_devices",
        sa.Column("last_connection_latency_ms", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_netops_devices_last_connection_status",
        "netops_devices",
        ["last_connection_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_netops_devices_last_connection_status", table_name="netops_devices")
    op.drop_column("netops_devices", "last_connection_latency_ms")
    op.drop_column("netops_devices", "last_connection_error")
    op.drop_column("netops_devices", "last_connection_failure_code")
    op.drop_column("netops_devices", "last_connection_checked_at")
    op.drop_column("netops_devices", "last_connection_status")
