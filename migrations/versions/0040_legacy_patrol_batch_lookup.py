"""Index legacy patrol runs for batch-history compatibility reads.

Revision ID: 0040_legacy_patrol_lookup
Revises: 0039_batch_history_pagination
"""

from alembic import op

revision = "0040_legacy_patrol_lookup"
down_revision = "0039_batch_history_pagination"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_workflow_runs_legacy_patrol_batch_created
        ON netops_workflow_runs ((context ->> 'patrol_batch_id'), created_at DESC, id)
        WHERE template_name = 'patrol-change-event'
          AND source = 'device-management'
          AND context ->> 'patrol_batch_id' IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP INDEX IF EXISTS ix_workflow_runs_legacy_patrol_batch_created"
    )
