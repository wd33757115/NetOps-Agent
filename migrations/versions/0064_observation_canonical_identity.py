"""Add canonical entity projection to ObservationContract.

Revision ID: 0064_contract_entity_identity
Revises: 0063_truth_qualification
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0064_contract_entity_identity"
down_revision = "0063_truth_qualification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "netops_observation_contracts",
        sa.Column("canonical_entity_type", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "netops_observation_contracts",
        sa.Column("entity_identity_fields_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "netops_observation_contracts",
        sa.Column("entity_dimensions_json", sa.JSON(), nullable=True),
    )
    op.create_index(
        "ix_observation_contract_canonical_entity",
        "netops_observation_contracts",
        ["canonical_entity_type"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_observation_contract_canonical_entity",
        table_name="netops_observation_contracts",
    )
    op.drop_column("netops_observation_contracts", "entity_dimensions_json")
    op.drop_column(
        "netops_observation_contracts",
        "entity_identity_fields_json",
    )
    op.drop_column("netops_observation_contracts", "canonical_entity_type")
