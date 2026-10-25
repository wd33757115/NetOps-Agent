"""Add the compact command-adapter specification.

Revision ID: 0096_command_adapter_spec
Revises: 0095_legacy_observations
"""

import sqlalchemy as sa
from alembic import op


revision = "0096_command_adapter_spec"
down_revision = "0095_legacy_observations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "netops_collection_implementation_versions",
        sa.Column("adapter_spec_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column(
        "netops_collection_implementation_versions",
        "adapter_spec_json",
    )
