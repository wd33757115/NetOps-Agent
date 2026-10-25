"""Scope generated rule assets to a CheckItem version.

Revision ID: 0099_check_item_rule_asset_scope
Revises: 0098_command_adapter_projection_assets
"""

import sqlalchemy as sa
from alembic import op

revision = "0099_check_item_rule_asset_scope"
down_revision = "0098_command_adapter_projection_assets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "netops_change_policy_versions",
        sa.Column("check_item_version_id", sa.String(length=64), nullable=True),
    )
    op.create_foreign_key(
        "fk_change_policy_version_check_item_version",
        "netops_change_policy_versions",
        "netops_check_item_versions",
        ["check_item_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_netops_change_policy_versions_check_item_version_id",
        "netops_change_policy_versions",
        ["check_item_version_id"],
    )
    op.drop_index(
        "uq_change_policy_active_observation_type",
        table_name="netops_change_policy_versions",
    )
    op.create_index(
        "uq_change_policy_active_observation_type",
        "netops_change_policy_versions",
        ["check_item_version_id", "observation_type"],
        unique=True,
        postgresql_where=sa.text(
            "status = 'active' AND check_item_version_id IS NOT NULL"
        ),
    )
    op.create_index(
        "uq_change_policy_active_observation_type_legacy",
        "netops_change_policy_versions",
        ["observation_type"],
        unique=True,
        postgresql_where=sa.text(
            "status = 'active' AND check_item_version_id IS NULL"
        ),
    )

    op.add_column(
        "netops_event_rule_versions",
        sa.Column("check_item_version_id", sa.String(length=64), nullable=True),
    )
    op.create_foreign_key(
        "fk_event_rule_version_check_item_version",
        "netops_event_rule_versions",
        "netops_check_item_versions",
        ["check_item_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_netops_event_rule_versions_check_item_version_id",
        "netops_event_rule_versions",
        ["check_item_version_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_netops_event_rule_versions_check_item_version_id",
        table_name="netops_event_rule_versions",
    )
    op.drop_constraint(
        "fk_event_rule_version_check_item_version",
        "netops_event_rule_versions",
        type_="foreignkey",
    )
    op.drop_column("netops_event_rule_versions", "check_item_version_id")

    op.drop_index(
        "uq_change_policy_active_observation_type_legacy",
        table_name="netops_change_policy_versions",
    )
    op.drop_index(
        "uq_change_policy_active_observation_type",
        table_name="netops_change_policy_versions",
    )
    op.create_index(
        "uq_change_policy_active_observation_type",
        "netops_change_policy_versions",
        ["observation_type"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.drop_index(
        "ix_netops_change_policy_versions_check_item_version_id",
        table_name="netops_change_policy_versions",
    )
    op.drop_constraint(
        "fk_change_policy_version_check_item_version",
        "netops_change_policy_versions",
        type_="foreignkey",
    )
    op.drop_column("netops_change_policy_versions", "check_item_version_id")
