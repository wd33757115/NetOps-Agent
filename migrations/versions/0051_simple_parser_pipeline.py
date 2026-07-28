"""Add durable state for the Simple Parser generation pipeline.

Revision ID: 0051_simple_parser_pipeline
Revises: 0050_raw_evidence_source
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0051_simple_parser_pipeline"
down_revision = "0050_raw_evidence_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "parser_template_jobs",
        sa.Column(
            "pipeline_version",
            sa.String(length=32),
            nullable=False,
            server_default="legacy",
        ),
    )
    op.add_column(
        "parser_template_jobs",
        sa.Column(
            "parser_spec_status",
            sa.String(length=32),
            nullable=False,
            server_default="missing",
        ),
    )
    op.add_column(
        "parser_template_jobs",
        sa.Column("parser_spec_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "parser_template_jobs",
        sa.Column("parser_spec_sha256", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_parser_template_jobs_pipeline_version",
        "parser_template_jobs",
        ["pipeline_version"],
    )
    op.create_index(
        "ix_parser_template_jobs_parser_spec_status",
        "parser_template_jobs",
        ["parser_spec_status"],
    )
    op.create_index(
        "ix_parser_template_jobs_parser_spec_sha256",
        "parser_template_jobs",
        ["parser_spec_sha256"],
    )

    op.add_column(
        "parser_job_samples",
        sa.Column(
            "sample_role",
            sa.String(length=32),
            nullable=False,
            server_default="unassigned",
        ),
    )
    op.add_column(
        "parser_job_samples",
        sa.Column("expectation_json", sa.JSON(), nullable=True),
    )
    op.create_index(
        "ix_parser_job_samples_sample_role",
        "parser_job_samples",
        ["sample_role"],
    )


def downgrade() -> None:
    op.drop_index("ix_parser_job_samples_sample_role", table_name="parser_job_samples")
    op.drop_column("parser_job_samples", "expectation_json")
    op.drop_column("parser_job_samples", "sample_role")
    op.drop_index(
        "ix_parser_template_jobs_parser_spec_sha256",
        table_name="parser_template_jobs",
    )
    op.drop_index(
        "ix_parser_template_jobs_parser_spec_status",
        table_name="parser_template_jobs",
    )
    op.drop_index(
        "ix_parser_template_jobs_pipeline_version",
        table_name="parser_template_jobs",
    )
    op.drop_column("parser_template_jobs", "parser_spec_sha256")
    op.drop_column("parser_template_jobs", "parser_spec_json")
    op.drop_column("parser_template_jobs", "parser_spec_status")
    op.drop_column("parser_template_jobs", "pipeline_version")
