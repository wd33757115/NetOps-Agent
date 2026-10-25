"""enforce PostgreSQL-only parser job samples

Revision ID: 0009_pg_parser_samples
Revises: 0008_drop_sample_db_path
Create Date: 2026-06-15
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0009_pg_parser_samples"
down_revision: Union[str, Sequence[str], None] = "0008_drop_sample_db_path"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Keep the retired job audit records, but remove sample links that no
    # longer identify an immutable PostgreSQL snapshot.
    op.execute(
        """
        DELETE FROM parser_job_samples AS sample
        WHERE sample.snapshot_source <> 'postgresql'
           OR NOT EXISTS (
                SELECT 1
                FROM netops_command_snapshots AS snapshot
                WHERE snapshot.id = sample.snapshot_id
           )
        """
    )
    op.execute(
        """
        UPDATE parser_template_jobs AS job
        SET sample_count = (
            SELECT COUNT(*)
            FROM parser_job_samples AS sample
            WHERE sample.job_id = job.id
        )
        """
    )
    op.create_foreign_key(
        "fk_parser_job_samples_snapshot_id",
        "parser_job_samples",
        "netops_command_snapshots",
        ["snapshot_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_check_constraint(
        "ck_parser_job_samples_postgresql_source",
        "parser_job_samples",
        "snapshot_source = 'postgresql'",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_parser_job_samples_postgresql_source",
        "parser_job_samples",
        type_="check",
    )
    op.drop_constraint(
        "fk_parser_job_samples_snapshot_id",
        "parser_job_samples",
        type_="foreignkey",
    )
