"""Add the production comparison baseline lookup index.

Revision ID: 0091_comparison_baseline_idx
Revises: 0090_assessment_policy_cutover
"""

from alembic import op

revision = "0091_comparison_baseline_idx"
down_revision = "0090_assessment_policy_cutover"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_check_execution_baseline_lookup",
        "netops_check_executions",
        [
            "device_id",
            "check_item_version_id",
            "observation_max_at",
            "execution_status",
        ],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_check_execution_baseline_lookup",
        table_name="netops_check_executions",
    )
