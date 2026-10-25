"""Add durable Parser Template Agent attempts.

Revision ID: 0042_parser_generation_attempts
Revises: 0041_offline_patrol_jobs
"""

import sqlalchemy as sa
from alembic import op

revision = "0042_parser_generation_attempts"
down_revision = "0041_offline_patrol_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "parser_template_jobs",
        sa.Column("contract_revision_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "parser_template_jobs",
        sa.Column("template_attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "parser_template_jobs",
        sa.Column("best_attempt_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "parser_template_jobs",
        sa.Column("agent_state_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "parser_template_jobs",
        sa.Column("agent_budget_json", sa.JSON(), nullable=True),
    )
    op.create_index(
        "ix_parser_template_jobs_best_attempt_id",
        "parser_template_jobs",
        ["best_attempt_id"],
    )

    op.create_table(
        "parser_generation_attempts",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(length=64),
            sa.ForeignKey("parser_template_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "attempt_number",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "parent_attempt_id",
            sa.String(length=64),
            sa.ForeignKey("parser_generation_attempts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("model_provider", sa.String(length=64), nullable=True),
        sa.Column("model_name", sa.String(length=128), nullable=True),
        sa.Column("textfsm_runtime_version", sa.String(length=32), nullable=False),
        sa.Column("dialect_version", sa.String(length=64), nullable=False),
        sa.Column("dialect_hash", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=True),
        sa.Column("prompt_hash", sa.String(length=64), nullable=True),
        sa.Column("contract_hash", sa.String(length=64), nullable=True),
        sa.Column("train_sample_set_hash", sa.String(length=64), nullable=True),
        sa.Column("holdout_sample_set_hash", sa.String(length=64), nullable=True),
        sa.Column("template_text", sa.Text(), nullable=True),
        sa.Column("template_sha256", sa.String(length=64), nullable=True),
        sa.Column("validation_report_json", sa.JSON(), nullable=False),
        sa.Column("failure_signature", sa.String(length=64), nullable=True),
        sa.Column("candidate_rank", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("token_usage_json", sa.JSON(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "uq_parser_generation_attempt_number",
        "parser_generation_attempts",
        ["job_id", "attempt_number"],
        unique=True,
    )
    op.create_index(
        "ix_parser_generation_attempt_job_created",
        "parser_generation_attempts",
        ["job_id", "created_at"],
    )
    op.create_index(
        "ix_parser_generation_attempts_parent_attempt_id",
        "parser_generation_attempts",
        ["parent_attempt_id"],
    )
    op.create_index(
        "ix_parser_generation_attempts_stage",
        "parser_generation_attempts",
        ["stage"],
    )
    op.create_index(
        "ix_parser_generation_attempts_status",
        "parser_generation_attempts",
        ["status"],
    )
    op.create_index(
        "ix_parser_generation_attempts_template_sha256",
        "parser_generation_attempts",
        ["template_sha256"],
    )
    op.create_index(
        "ix_parser_generation_attempts_failure_signature",
        "parser_generation_attempts",
        ["failure_signature"],
    )


def downgrade() -> None:
    op.drop_table("parser_generation_attempts")
    op.drop_index("ix_parser_template_jobs_best_attempt_id", table_name="parser_template_jobs")
    op.drop_column("parser_template_jobs", "agent_budget_json")
    op.drop_column("parser_template_jobs", "agent_state_json")
    op.drop_column("parser_template_jobs", "best_attempt_id")
    op.drop_column("parser_template_jobs", "template_attempt_count")
    op.drop_column("parser_template_jobs", "contract_revision_count")
