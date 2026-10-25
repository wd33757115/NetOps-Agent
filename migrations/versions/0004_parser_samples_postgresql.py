"""move parser job sample references to PostgreSQL

Revision ID: 0004_parser_samples_postgresql
Revises: 0003_schema_reconciliation
Create Date: 2026-06-15
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_parser_samples_postgresql"
down_revision: Union[str, Sequence[str], None] = "0003_schema_reconciliation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "parser_job_samples",
        sa.Column("snapshot_source", sa.String(length=32), nullable=True),
    )
    op.execute(
        """
        UPDATE parser_job_samples AS sample
        SET snapshot_source = CASE
            WHEN EXISTS (
                SELECT 1
                FROM netops_command_snapshots AS snapshot
                WHERE snapshot.id = sample.snapshot_id
            )
            THEN 'postgresql'
            ELSE 'sqlite_legacy'
        END
        """
    )
    op.alter_column(
        "parser_job_samples",
        "snapshot_source",
        existing_type=sa.String(length=32),
        nullable=False,
        server_default="postgresql",
    )
    op.alter_column(
        "parser_job_samples",
        "snapshot_db_path",
        existing_type=sa.String(length=1024),
        nullable=True,
    )
    op.create_index(
        "ix_parser_job_samples_snapshot_source",
        "parser_job_samples",
        ["snapshot_source"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_parser_job_samples_snapshot_source",
        table_name="parser_job_samples",
    )
    op.execute(
        """
        UPDATE parser_job_samples
        SET snapshot_db_path = COALESCE(snapshot_db_path, 'postgresql')
        """
    )
    op.alter_column(
        "parser_job_samples",
        "snapshot_db_path",
        existing_type=sa.String(length=1024),
        nullable=False,
    )
    op.drop_column("parser_job_samples", "snapshot_source")
