"""Add governed production Parser sample sanitization.

Revision ID: 0033_parser_sample_sanitization
Revises: 0032_patrol_status_dimensions
"""

import sqlalchemy as sa
from alembic import op

revision = "0033_parser_sample_sanitization"
down_revision = "0032_patrol_status_dimensions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "parser_template_jobs",
        sa.Column(
            "source_mode",
            sa.String(32),
            server_default="parser_validation",
            nullable=False,
        ),
    )
    op.create_index(
        "ix_parser_template_jobs_source_mode",
        "parser_template_jobs",
        ["source_mode"],
    )
    op.add_column(
        "parser_job_samples",
        sa.Column(
            "sanitization_status",
            sa.String(32),
            server_default="pending_sanitization",
            nullable=False,
        ),
    )
    op.add_column(
        "parser_job_samples",
        sa.Column("sanitization_version", sa.String(32), nullable=True),
    )
    op.add_column(
        "parser_job_samples",
        sa.Column("sanitized_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "parser_job_samples",
        sa.Column("sanitized_hash", sa.String(64), nullable=True),
    )
    op.add_column(
        "parser_job_samples",
        sa.Column("redaction_summary_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "parser_job_samples",
        sa.Column("sanitized_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_parser_job_samples_sanitization_status",
        "parser_job_samples",
        ["sanitization_status"],
    )
    op.create_index(
        "ix_parser_job_samples_sanitized_hash",
        "parser_job_samples",
        ["sanitized_hash"],
    )
    op.execute(
        """
        UPDATE parser_job_samples
        SET sanitization_status = 'sanitized',
            sanitization_version = 'trusted-validation-v1',
            sanitized_at = created_at
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_parser_job_samples_sanitized_hash",
        table_name="parser_job_samples",
    )
    op.drop_index(
        "ix_parser_job_samples_sanitization_status",
        table_name="parser_job_samples",
    )
    for name in (
        "sanitized_at",
        "redaction_summary_json",
        "sanitized_hash",
        "sanitized_text",
        "sanitization_version",
        "sanitization_status",
    ):
        op.drop_column("parser_job_samples", name)
    op.drop_index(
        "ix_parser_template_jobs_source_mode",
        table_name="parser_template_jobs",
    )
    op.drop_column("parser_template_jobs", "source_mode")
