"""Make Simple Parser v2 the only default for new jobs.

Revision ID: 0052_simple_parser_only
Revises: 0051_simple_parser_pipeline
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0052_simple_parser_only"
down_revision = "0051_simple_parser_pipeline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "parser_template_jobs",
        "pipeline_version",
        existing_type=sa.String(length=32),
        existing_nullable=False,
        server_default="simple_v2",
    )


def downgrade() -> None:
    op.alter_column(
        "parser_template_jobs",
        "pipeline_version",
        existing_type=sa.String(length=32),
        existing_nullable=False,
        server_default="legacy",
    )
