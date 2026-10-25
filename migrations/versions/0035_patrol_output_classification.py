"""Persist patrol output classification.

Revision ID: 0035_output_classification
Revises: 0034_parser_backfill_jobs
"""

import sqlalchemy as sa
from alembic import op

revision = "0035_output_classification"
down_revision = "0034_parser_backfill_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("netops_command_snapshots", "netops_patrol_collection_results"):
        op.add_column(
            table,
            sa.Column(
                "output_status",
                sa.String(32),
                nullable=True,
                server_default="not_classified",
            ),
        )
        op.execute(
            sa.text(
                f"""
                UPDATE {table}
                SET output_status = CASE
                  WHEN parse_status = 'parse_empty' THEN 'valid_empty'
                  WHEN parse_status = 'empty_unverified' THEN 'empty_unverified'
                  WHEN parse_status = 'not_applicable' THEN 'unsupported_command'
                  WHEN parse_status = 'command_error' THEN 'command_error'
                  WHEN parse_status = 'ignored' THEN 'not_classified'
                  ELSE 'data'
                END
                """
            )
        )
        op.alter_column(table, "output_status", nullable=False)
        op.create_index(f"ix_{table}_output_status", table, ["output_status"])


def downgrade() -> None:
    for table in ("netops_patrol_collection_results", "netops_command_snapshots"):
        op.drop_index(f"ix_{table}_output_status", table_name=table)
        op.drop_column(table, "output_status")
