"""reconcile legacy PostgreSQL schema drift

Revision ID: 0003_schema_reconciliation
Revises: 0002_patrol_data_plane
Create Date: 2026-06-15
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0003_schema_reconciliation"
down_revision: Union[str, Sequence[str], None] = "0002_patrol_data_plane"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_storage_folder_private_root
        ON netops_storage_folders (owner_id)
        WHERE parent_id IS NULL
          AND visibility = 'private'
          AND is_deleted = false
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_storage_folder_shared_root
        ON netops_storage_folders (team_id)
        WHERE parent_id IS NULL
          AND visibility = 'shared'
          AND is_deleted = false
        """
    )
    op.execute(
        "DROP INDEX IF EXISTS ix_parser_template_jobs_superseded_by_job_id"
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_parser_template_jobs_superseded_by
        ON parser_template_jobs (superseded_by_job_id)
        """
    )
    op.execute(
        "ALTER TABLE parser_templates ALTER COLUMN contract_version SET DEFAULT 1"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE parser_templates ALTER COLUMN contract_version DROP DEFAULT"
    )
    op.execute(
        "DROP INDEX IF EXISTS ix_parser_template_jobs_superseded_by"
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_parser_template_jobs_superseded_by_job_id
        ON parser_template_jobs (superseded_by_job_id)
        """
    )
