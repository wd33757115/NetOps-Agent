"""Add stable patrol batch history pagination index.

Revision ID: 0039_batch_history_pagination
Revises: 0038_patrol_batch_manifest
"""

from alembic import op

revision = "0039_batch_history_pagination"
down_revision = "0038_patrol_batch_manifest"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_netops_patrol_batches_archive_created_id",
        "netops_patrol_batches",
        ["archived", "created_at", "patrol_batch_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_netops_patrol_batches_archive_created_id",
        table_name="netops_patrol_batches",
    )
