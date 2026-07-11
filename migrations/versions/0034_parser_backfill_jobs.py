"""Add explicit dry-run Parser backfill jobs.

Revision ID: 0034_parser_backfill_jobs
Revises: 0033_parser_sample_sanitization
"""

import sqlalchemy as sa
from alembic import op

revision = "0034_parser_backfill_jobs"
down_revision = "0033_parser_sample_sanitization"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "parser_backfill_jobs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column(
            "template_id",
            sa.String(64),
            sa.ForeignKey("parser_templates.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("dry_run", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("filters_json", sa.JSON(), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=True),
        sa.Column("requested_by", sa.String(128), nullable=False),
        sa.Column("approved_by", sa.String(128), nullable=True),
        sa.Column("approval_reason", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("owner_token", sa.String(64), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_parser_backfill_jobs_idempotency_key",
        "parser_backfill_jobs",
        ["idempotency_key"],
        unique=True,
    )
    op.create_index(
        "ix_parser_backfill_jobs_template_id",
        "parser_backfill_jobs",
        ["template_id"],
    )
    op.create_index("ix_parser_backfill_jobs_status", "parser_backfill_jobs", ["status"])
    op.create_index(
        "ix_parser_backfill_jobs_owner_token",
        "parser_backfill_jobs",
        ["owner_token"],
    )
    op.create_index(
        "ix_parser_backfill_jobs_lease_until",
        "parser_backfill_jobs",
        ["lease_until"],
    )
    op.create_table(
        "parser_backfill_results",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(64),
            sa.ForeignKey("parser_backfill_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "snapshot_id",
            sa.String(64),
            sa.ForeignKey("netops_command_snapshots.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("parse_status", sa.String(32), nullable=False),
        sa.Column("structured_json", sa.JSON(), nullable=True),
        sa.Column("failure_code", sa.String(128), nullable=True),
        sa.Column("observation_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("existing_observation_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("committed_observation_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_parser_backfill_results_job_id", "parser_backfill_results", ["job_id"])
    op.create_index(
        "ix_parser_backfill_results_snapshot_id",
        "parser_backfill_results",
        ["snapshot_id"],
    )
    op.create_index(
        "ix_parser_backfill_results_parse_status",
        "parser_backfill_results",
        ["parse_status"],
    )
    op.create_index(
        "uq_parser_backfill_job_snapshot",
        "parser_backfill_results",
        ["job_id", "snapshot_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("parser_backfill_results")
    op.drop_table("parser_backfill_jobs")
