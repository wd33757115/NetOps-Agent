"""Separate current Parser samples from retained historical evidence.

Revision ID: 0082_sample_lifecycle
Revises: 0081_sample_count
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0082_sample_lifecycle"
down_revision = "0081_sample_count"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "parser_job_samples",
        sa.Column(
            "lifecycle_status",
            sa.String(length=32),
            nullable=False,
            server_default="current",
        ),
    )
    op.add_column(
        "parser_job_samples",
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "parser_job_samples",
        sa.Column("retirement_reason", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        "ck_parser_job_samples_lifecycle_status",
        "parser_job_samples",
        "lifecycle_status IN ('current', 'retired')",
    )
    op.create_index(
        op.f("ix_parser_job_samples_lifecycle_status"),
        "parser_job_samples",
        ["lifecycle_status"],
    )

    # A retried offline import can leave samples from an earlier PatrolRun in
    # the same durable Parser Job. Retain them for audit, but remove them from
    # the current generation input set.
    op.execute(
        sa.text(
            """
            UPDATE parser_job_samples AS sample
               SET lifecycle_status = 'retired',
                   retired_at = CURRENT_TIMESTAMP,
                   retirement_reason = 'superseded offline source attempt'
              FROM netops_command_snapshots AS snapshot,
                   netops_offline_patrol_periods AS period
             WHERE sample.snapshot_id = snapshot.id
               AND sample.dataset_id = period.dataset_id
               AND period.patrol_run_id IS NOT NULL
               AND snapshot.run_id <> period.patrol_run_id
               AND sample.lifecycle_status = 'current'
            """
        )
    )

    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS trg_parser_job_sample_count ON parser_job_samples"
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
                              AND lifecycle_status = 'current'
                       )
                     WHERE id = OLD.job_id;
                END IF;
                IF TG_OP IN ('INSERT', 'UPDATE') THEN
                    UPDATE parser_template_jobs
                       SET sample_count = (
                           SELECT COUNT(*)
                             FROM parser_job_samples
                            WHERE job_id = NEW.job_id
                              AND lifecycle_status = 'current'
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
            AFTER INSERT OR DELETE OR UPDATE OF job_id, lifecycle_status
            ON parser_job_samples
            FOR EACH ROW
            EXECUTE FUNCTION sync_parser_job_sample_count()
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE parser_template_jobs AS job
               SET sample_count = (
                   SELECT COUNT(*)
                     FROM parser_job_samples AS sample
                    WHERE sample.job_id = job.id
                      AND sample.lifecycle_status = 'current'
               )
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS trg_parser_job_sample_count ON parser_job_samples"
        )
    )
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
    op.drop_index(
        op.f("ix_parser_job_samples_lifecycle_status"),
        table_name="parser_job_samples",
    )
    op.drop_constraint(
        "ck_parser_job_samples_lifecycle_status",
        "parser_job_samples",
        type_="check",
    )
    op.drop_column("parser_job_samples", "retirement_reason")
    op.drop_column("parser_job_samples", "retired_at")
    op.drop_column("parser_job_samples", "lifecycle_status")
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
