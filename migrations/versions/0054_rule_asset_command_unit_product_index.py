"""Index the product-facing command asset projection.

Revision ID: 0054_rule_asset_product_index
Revises: 0053_parser_embedded_content
"""

from __future__ import annotations

from alembic import op

revision = "0054_rule_asset_product_index"
down_revision = "0053_parser_embedded_content"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_rule_asset_command_unit_asset_updated",
        "netops_rule_asset_command_units",
        ["asset_key", "updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_rule_asset_command_unit_asset_updated",
        table_name="netops_rule_asset_command_units",
    )
