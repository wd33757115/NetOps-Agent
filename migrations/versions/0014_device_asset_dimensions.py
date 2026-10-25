"""add device asset dimensions

Revision ID: 0014_device_asset_dims
Revises: 0013_device_conn_health_history
Create Date: 2026-06-21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0014_device_asset_dims"
down_revision: Union[str, Sequence[str], None] = "0013_device_conn_health_history"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "netops_devices",
        sa.Column("platform", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "netops_devices",
        sa.Column("device_type", sa.String(length=128), nullable=True),
    )
    op.create_index("ix_netops_devices_platform", "netops_devices", ["platform"])
    op.create_index("ix_netops_devices_device_type", "netops_devices", ["device_type"])


def downgrade() -> None:
    op.drop_index("ix_netops_devices_device_type", table_name="netops_devices")
    op.drop_index("ix_netops_devices_platform", table_name="netops_devices")
    op.drop_column("netops_devices", "device_type")
    op.drop_column("netops_devices", "platform")
