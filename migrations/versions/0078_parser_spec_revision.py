"""Add immutable ParserSpec revision history.

Revision ID: 0078_parser_spec_revision
Revises: 0077_report_claim_ledger
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0078_parser_spec_revision"
down_revision = "0077_report_claim_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "parser_template_jobs",
        sa.Column(
            "parser_spec_revision",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_table(
        "netops_parser_spec_revisions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(length=64),
            sa.ForeignKey("parser_template_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("parser_spec_status", sa.String(length=32), nullable=False),
        sa.Column("parser_spec_json", sa.JSON(), nullable=False),
        sa.Column("parser_spec_sha256", sa.String(length=64), nullable=False),
        sa.Column("previous_parser_spec_sha256", sa.String(length=64), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "invalidated_template_ids_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "parser_spec_status IN ('draft', 'confirmed')",
            name="ck_parser_spec_revision_status",
        ),
        sa.CheckConstraint(
            "source IN ('engineer', 'llm_suggestion')",
            name="ck_parser_spec_revision_source",
        ),
    )
    op.create_index(
        op.f("ix_netops_parser_spec_revisions_job_id"),
        "netops_parser_spec_revisions",
        ["job_id"],
    )
    op.create_index(
        op.f("ix_netops_parser_spec_revisions_parser_spec_sha256"),
        "netops_parser_spec_revisions",
        ["parser_spec_sha256"],
    )
    op.create_index(
        "uq_parser_spec_revision_job_number",
        "netops_parser_spec_revisions",
        ["job_id", "revision_number"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("netops_parser_spec_revisions")
    op.drop_column("parser_template_jobs", "parser_spec_revision")
