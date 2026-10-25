"""Widen immutable canonical schema references used by comparisons.

Revision ID: 0062_comparison_schema_ref
Revises: 0061_rule_asset_check_item
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0062_comparison_schema_ref"
down_revision = "0061_rule_asset_check_item"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "netops_comparison_contexts",
        "canonical_schema_version",
        existing_type=sa.String(length=64),
        type_=sa.String(length=128),
        existing_nullable=True,
    )


def downgrade() -> None:
    connection = op.get_bind()
    oversized = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM netops_comparison_contexts "
            "WHERE length(canonical_schema_version) > 64"
        )
    ).scalar_one()
    if int(oversized or 0) > 0:
        raise RuntimeError(
            "cannot downgrade canonical_schema_version to 64 characters "
            "while full catalog schema references exist"
        )
    op.alter_column(
        "netops_comparison_contexts",
        "canonical_schema_version",
        existing_type=sa.String(length=128),
        type_=sa.String(length=64),
        existing_nullable=True,
    )
