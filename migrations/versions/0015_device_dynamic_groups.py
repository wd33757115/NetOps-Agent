"""add device dynamic groups

Revision ID: 0015_device_dynamic_groups
Revises: 0014_device_asset_dims
Create Date: 2026-06-21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0015_device_dynamic_groups"
down_revision: Union[str, Sequence[str], None] = "0014_device_asset_dims"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "netops_device_dynamic_groups",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("selector_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_netops_device_dynamic_groups_name"),
    )
    op.create_index(
        "ix_netops_device_dynamic_groups_name",
        "netops_device_dynamic_groups",
        ["name"],
    )
    op.create_index(
        "ix_netops_device_dynamic_groups_status",
        "netops_device_dynamic_groups",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_netops_device_dynamic_groups_status", table_name="netops_device_dynamic_groups")
    op.drop_index("ix_netops_device_dynamic_groups_name", table_name="netops_device_dynamic_groups")
    op.drop_table("netops_device_dynamic_groups")
