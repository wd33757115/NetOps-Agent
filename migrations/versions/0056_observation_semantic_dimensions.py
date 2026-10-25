"""Persist canonical observation dimensions and runtime identity.

Revision ID: 0056_observation_dimensions
Revises: 0055_parser_model_call_budget
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0056_observation_dimensions"
down_revision = "0055_parser_model_call_budget"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "netops_state_observations",
        sa.Column("state_key", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "netops_state_observations",
        sa.Column("dimensions_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "netops_state_observations",
        sa.Column("dimension_signature", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "netops_current_state",
        sa.Column("dimensions_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "netops_current_state",
        sa.Column("dimension_signature", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_netops_state_observations_state_key",
        "netops_state_observations",
        ["state_key"],
        unique=False,
    )
    op.create_index(
        "ix_netops_state_observations_dimension_signature",
        "netops_state_observations",
        ["dimension_signature"],
        unique=False,
    )
    op.create_index(
        "ix_netops_current_state_dimension_signature",
        "netops_current_state",
        ["dimension_signature"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_netops_current_state_dimension_signature",
        table_name="netops_current_state",
    )
    op.drop_index(
        "ix_netops_state_observations_dimension_signature",
        table_name="netops_state_observations",
    )
    op.drop_index(
        "ix_netops_state_observations_state_key",
        table_name="netops_state_observations",
    )
    op.drop_column("netops_current_state", "dimension_signature")
    op.drop_column("netops_current_state", "dimensions_json")
    op.drop_column("netops_state_observations", "dimension_signature")
    op.drop_column("netops_state_observations", "dimensions_json")
    op.drop_column("netops_state_observations", "state_key")
