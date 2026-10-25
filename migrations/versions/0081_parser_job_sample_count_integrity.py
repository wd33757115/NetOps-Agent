"""Keep Parser Job sample counts consistent with source rows.

Revision ID: 0081_sample_count
Revises: 0080_candidate_evidence
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0081_sample_count"
down_revision = "0080_candidate_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE parser_template_jobs AS job
               SET sample_count = (
                   SELECT COUNT(*)
                     FROM parser_job_samples AS sample
                    WHERE sample.job_id = job.id
               )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION sync_parser_job_sample_count()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF TG_OP IN ('DELETE', 'UPDATE') THEN
                    UPDATE parser_template_jobs
                       SET sample_count = (
                           SELECT COUNT(*)
                             FROM parser_job_samples
                            WHERE job_id = OLD.job_id
                       )
                     WHERE id = OLD.job_id;
                END IF;
                IF TG_OP IN ('INSERT', 'UPDATE') THEN
                    UPDATE parser_template_jobs
                       SET sample_count = (
                           SELECT COUNT(*)
                             FROM parser_job_samples
                            WHERE job_id = NEW.job_id
                       )
                     WHERE id = NEW.job_id;
                END IF;
                RETURN NULL;
            END;
            $$
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_parser_job_sample_count
            AFTER INSERT OR DELETE OR UPDATE OF job_id
            ON parser_job_samples
            FOR EACH ROW
            EXECUTE FUNCTION sync_parser_job_sample_count()
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS trg_parser_job_sample_count ON parser_job_samples"
        )
    )
    op.execute(sa.text("DROP FUNCTION IF EXISTS sync_parser_job_sample_count()"))
