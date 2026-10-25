"""Add durable legacy-path observations.

Revision ID: 0095_legacy_observations
Revises: 0094_catalog_cutover
"""

import sqlalchemy as sa
from alembic import op

revision = "0095_legacy_observations"
down_revision = "0094_catalog_cutover"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "netops_legacy_path_observations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("candidate_id", sa.String(length=128), nullable=False),
        sa.Column("operation", sa.String(length=128), nullable=False),
        sa.Column("outcome", sa.String(length=64), nullable=False),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_netops_legacy_path_observations_candidate_id",
        "netops_legacy_path_observations",
        ["candidate_id"],
    )
    op.create_index(
        "ix_netops_legacy_path_observations_observed_at",
        "netops_legacy_path_observations",
        ["observed_at"],
    )
    op.create_index(
        "ix_legacy_path_candidate_observed",
        "netops_legacy_path_observations",
        ["candidate_id", "observed_at"],
    )
    op.create_index(
        "ix_legacy_path_operation_observed",
        "netops_legacy_path_observations",
        ["operation", "observed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_legacy_path_operation_observed",
        table_name="netops_legacy_path_observations",
    )
    op.drop_index(
        "ix_legacy_path_candidate_observed",
        table_name="netops_legacy_path_observations",
    )
    op.drop_index(
        "ix_netops_legacy_path_observations_observed_at",
        table_name="netops_legacy_path_observations",
    )
    op.drop_index(
        "ix_netops_legacy_path_observations_candidate_id",
        table_name="netops_legacy_path_observations",
    )
    op.drop_table("netops_legacy_path_observations")
