"""Add a durable per-job Parser model-call budget and execution ledger.

Revision ID: 0055_parser_model_call_budget
Revises: 0054_rule_asset_product_index
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0055_parser_model_call_budget"
down_revision = "0054_rule_asset_product_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "parser_template_jobs",
        sa.Column(
            "model_calls_used",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_table(
        "parser_model_call_ledger",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("call_number", sa.Integer(), nullable=False),
        sa.Column("generation_run_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("model_provider", sa.String(length=64), nullable=True),
        sa.Column("model_name", sa.String(length=128), nullable=True),
        sa.Column("protocol_version", sa.String(length=64), nullable=True),
        sa.Column("candidate_sha256", sa.String(length=64), nullable=True),
        sa.Column("failure_signature", sa.String(length=64), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("token_usage_json", sa.JSON(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["parser_template_jobs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_parser_model_call_ledger_generation_run_id",
        "parser_model_call_ledger",
        ["generation_run_id"],
        unique=False,
    )
    op.create_index(
        "ix_parser_model_call_ledger_status",
        "parser_model_call_ledger",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_parser_model_call_ledger_candidate_sha256",
        "parser_model_call_ledger",
        ["candidate_sha256"],
        unique=False,
    )
    op.create_index(
        "ix_parser_model_call_ledger_failure_signature",
        "parser_model_call_ledger",
        ["failure_signature"],
        unique=False,
    )
    op.create_index(
        "ix_parser_model_call_job_created",
        "parser_model_call_ledger",
        ["job_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "uq_parser_model_call_job_number",
        "parser_model_call_ledger",
        ["job_id", "call_number"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_parser_model_call_job_number",
        table_name="parser_model_call_ledger",
    )
    op.drop_index(
        "ix_parser_model_call_job_created",
        table_name="parser_model_call_ledger",
    )
    op.drop_index(
        "ix_parser_model_call_ledger_failure_signature",
        table_name="parser_model_call_ledger",
    )
    op.drop_index(
        "ix_parser_model_call_ledger_candidate_sha256",
        table_name="parser_model_call_ledger",
    )
    op.drop_index(
        "ix_parser_model_call_ledger_status",
        table_name="parser_model_call_ledger",
    )
    op.drop_index(
        "ix_parser_model_call_ledger_generation_run_id",
        table_name="parser_model_call_ledger",
    )
    op.drop_table("parser_model_call_ledger")
    op.drop_column("parser_template_jobs", "model_calls_used")
