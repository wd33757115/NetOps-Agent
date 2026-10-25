"""Store new Parser candidates as immutable PostgreSQL content.

Revision ID: 0053_parser_embedded_content
Revises: 0052_simple_parser_only
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0053_parser_embedded_content"
down_revision = "0052_simple_parser_only"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "parser_templates",
        sa.Column("template_text", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("parser_templates", "template_text")
