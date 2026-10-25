"""add device tombstones

Revision ID: 0021_device_tombstones
Revises: 0020_collection_execution_plans
Create Date: 2026-06-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0021_device_tombstones"
down_revision: Union[str, Sequence[str], None] = "0020_collection_execution_plans"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "netops_device_tombstones",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("device_name", sa.String(length=256), nullable=False),
        sa.Column("management_ip", sa.String(length=64), nullable=False),
        sa.Column("vendor", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("family", sa.String(length=128), nullable=True),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "device_id",
        "device_name",
        "management_ip",
        "vendor",
        "model",
        "family",
        "deleted_at",
    ):
        op.create_index(
            f"ix_netops_device_tombstones_{column}",
            "netops_device_tombstones",
            [column],
        )


def downgrade() -> None:
    for column in (
        "deleted_at",
        "family",
        "model",
        "vendor",
        "management_ip",
        "device_name",
        "device_id",
    ):
        op.drop_index(
            f"ix_netops_device_tombstones_{column}",
            table_name="netops_device_tombstones",
        )
    op.drop_table("netops_device_tombstones")
