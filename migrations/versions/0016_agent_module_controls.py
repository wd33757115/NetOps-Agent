"""add agent module controls

Revision ID: 0016_agent_module_controls
Revises: 0015_device_dynamic_groups
Create Date: 2026-06-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0016_agent_module_controls"
down_revision: Union[str, Sequence[str], None] = "0015_device_dynamic_groups"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "netops_agent_module_controls",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("module_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("maintenance_reason", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('enabled','disabled','maintenance')",
            name="ck_agent_module_control_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("module_id", name="uq_netops_agent_module_controls_module_id"),
    )
    op.create_index(
        "ix_netops_agent_module_controls_module_id",
        "netops_agent_module_controls",
        ["module_id"],
    )
    op.create_index(
        "ix_netops_agent_module_controls_status",
        "netops_agent_module_controls",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_netops_agent_module_controls_status", table_name="netops_agent_module_controls")
    op.drop_index("ix_netops_agent_module_controls_module_id", table_name="netops_agent_module_controls")
    op.drop_table("netops_agent_module_controls")
