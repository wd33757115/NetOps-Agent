"""Add atomic catalog cutover evidence.

Revision ID: 0094_catalog_cutover
Revises: 0093_production_feedback
"""

import sqlalchemy as sa
from alembic import op

revision = "0094_catalog_cutover"
down_revision = "0093_production_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "netops_check_item_catalog_changes",
        sa.Column("rollout_campaign_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "netops_check_item_catalog_changes",
        sa.Column("cutover_bundle_version_ids_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "netops_check_item_catalog_changes",
        sa.Column("cutover_evidence_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "netops_check_item_catalog_changes",
        sa.Column("cutover_digest", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_check_item_catalog_change_rollout",
        "netops_check_item_catalog_changes",
        ["rollout_campaign_id"],
    )
    op.create_index(
        "ix_check_item_catalog_change_cutover_digest",
        "netops_check_item_catalog_changes",
        ["cutover_digest"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_check_item_catalog_change_cutover_digest",
        table_name="netops_check_item_catalog_changes",
    )
    op.drop_index(
        "ix_check_item_catalog_change_rollout",
        table_name="netops_check_item_catalog_changes",
    )
    op.drop_column("netops_check_item_catalog_changes", "cutover_digest")
    op.drop_column("netops_check_item_catalog_changes", "cutover_evidence_json")
    op.drop_column(
        "netops_check_item_catalog_changes",
        "cutover_bundle_version_ids_json",
    )
    op.drop_column("netops_check_item_catalog_changes", "rollout_campaign_id")
