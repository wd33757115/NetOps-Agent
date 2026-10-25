"""Add production parser gap ledger.

Revision ID: 0049_parser_gap_ledger
Revises: 0048_offline_job_purpose
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0049_parser_gap_ledger"
down_revision = "0048_offline_job_purpose"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "netops_parser_gap_ledger",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("vendor", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("family", sa.String(length=128), nullable=True),
        sa.Column("canonical_command", sa.String(length=256), nullable=False),
        sa.Column("failure_code", sa.String(length=64), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_run_id", sa.String(length=128), nullable=True),
        sa.Column("last_device_id", sa.String(length=256), nullable=True),
        sa.Column("draft_job_id", sa.String(length=64), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "uq_parser_gap_ledger_key",
        "netops_parser_gap_ledger",
        ["vendor", "canonical_command", "failure_code"],
        unique=True,
    )
    op.create_index(
        "ix_parser_gap_ledger_last_seen",
        "netops_parser_gap_ledger",
        ["last_seen_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_parser_gap_ledger_last_seen", table_name="netops_parser_gap_ledger")
    op.drop_index("uq_parser_gap_ledger_key", table_name="netops_parser_gap_ledger")
    op.drop_table("netops_parser_gap_ledger")
