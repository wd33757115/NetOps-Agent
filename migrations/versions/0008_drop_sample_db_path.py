"""drop obsolete parser sample database paths

Revision ID: 0008_drop_sample_db_path
Revises: 0007_change_event_indexes
Create Date: 2026-06-15
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008_drop_sample_db_path"
down_revision: Union[str, Sequence[str], None] = "0007_change_event_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("parser_job_samples", "snapshot_db_path")


def downgrade() -> None:
    op.add_column(
        "parser_job_samples",
        sa.Column("snapshot_db_path", sa.String(length=1024), nullable=True),
    )
