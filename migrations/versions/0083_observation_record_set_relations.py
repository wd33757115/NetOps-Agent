"""Persist validated ObservationContract record-set relations.

Revision ID: 0083_record_set_relations
Revises: 0082_sample_lifecycle
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0083_record_set_relations"
down_revision = "0082_sample_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "netops_observation_contracts",
        sa.Column("record_set_relations_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column(
        "netops_observation_contracts",
        "record_set_relations_json",
    )
