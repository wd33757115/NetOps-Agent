"""Add the check-item-centered rule asset production projection.

Revision ID: 0061_rule_asset_check_item
Revises: 0060_check_item_runtime_switch
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0061_rule_asset_check_item"
down_revision = "0060_check_item_runtime_switch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "netops_rule_asset_command_unit_check_items",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("command_unit_id", sa.String(length=64), nullable=False),
        sa.Column("check_item_id", sa.String(length=128), nullable=False),
        sa.Column("check_item_version_id", sa.String(length=64), nullable=False),
        sa.Column("implementation_id", sa.String(length=128), nullable=False),
        sa.Column("implementation_version_id", sa.String(length=64), nullable=False),
        sa.Column(
            "mapping_source",
            sa.String(length=32),
            nullable=False,
            server_default="catalog_resolver",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["command_unit_id"],
            ["netops_rule_asset_command_units.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["check_item_id"],
            ["netops_check_items.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["check_item_version_id"],
            ["netops_check_item_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["implementation_id"],
            ["netops_collection_implementations.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["implementation_version_id"],
            ["netops_collection_implementation_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_rule_asset_link_unit",
        "netops_rule_asset_command_unit_check_items",
        ["command_unit_id"],
    )
    op.create_index(
        "ix_rule_asset_link_check",
        "netops_rule_asset_command_unit_check_items",
        ["check_item_id"],
    )
    op.create_index(
        "ix_rule_asset_link_check_ver",
        "netops_rule_asset_command_unit_check_items",
        ["check_item_version_id"],
    )
    op.create_index(
        "ix_rule_asset_link_impl",
        "netops_rule_asset_command_unit_check_items",
        ["implementation_id"],
    )
    op.create_index(
        "ix_rule_asset_link_impl_ver",
        "netops_rule_asset_command_unit_check_items",
        ["implementation_version_id"],
    )
    op.create_index(
        "uq_rule_asset_command_unit_check_item",
        "netops_rule_asset_command_unit_check_items",
        ["command_unit_id", "check_item_id", "implementation_version_id"],
        unique=True,
    )
    op.create_index(
        "ix_rule_asset_check_item_implementation",
        "netops_rule_asset_command_unit_check_items",
        ["check_item_id", "implementation_version_id"],
    )


def downgrade() -> None:
    op.drop_table("netops_rule_asset_command_unit_check_items")
