"""add agent module maintenance window

Revision ID: 0017_agent_module_window
Revises: 0016_agent_module_controls
Create Date: 2026-06-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0017_agent_module_window"
down_revision: Union[str, Sequence[str], None] = "0016_agent_module_controls"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "netops_agent_module_controls",
        sa.Column("maintenance_starts_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "netops_agent_module_controls",
        sa.Column("maintenance_ends_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_netops_agent_module_controls_maintenance_window",
        "netops_agent_module_controls",
        ["maintenance_starts_at", "maintenance_ends_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_netops_agent_module_controls_maintenance_window",
        table_name="netops_agent_module_controls",
    )
    op.drop_column("netops_agent_module_controls", "maintenance_ends_at")
    op.drop_column("netops_agent_module_controls", "maintenance_starts_at")
