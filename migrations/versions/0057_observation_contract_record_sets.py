"""Persist multi-record ObservationContract definitions.

Revision ID: 0057_contract_record_sets
Revises: 0056_observation_dimensions
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0057_contract_record_sets"
down_revision = "0056_observation_dimensions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "netops_observation_contracts",
        sa.Column("record_sets_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("netops_observation_contracts", "record_sets_json")
