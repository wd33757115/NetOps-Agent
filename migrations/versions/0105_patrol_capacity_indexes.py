"""Indexes for scoped history and paginated offline job lists.

Revision ID: 0105_patrol_capacity_indexes
Revises: 0104_retire_module_controls
"""
from alembic import op
import sqlalchemy as sa

revision = '0105_patrol_capacity_indexes'
down_revision = '0104_retire_module_controls'
branch_labels = None
depends_on = None

INDEXES = (
    ('ix_offline_jobs_workspace_page', 'netops_offline_patrol_jobs',
     ['purpose', 'archived', sa.text('created_at DESC'), sa.text('id DESC')]),
    ('ix_offline_jobs_owner_page', 'netops_offline_patrol_jobs',
     ['owner_id', 'purpose', 'archived', sa.text('created_at DESC'), sa.text('id DESC')]),
    ('ix_state_observation_device_history', 'netops_state_observations',
     ['mode', 'device_id', sa.text('observed_at DESC')]),
)


def upgrade():
    # No long table write lock while an existing installation serves requests.
    with op.get_context().autocommit_block():
        for name, table, columns in INDEXES:
            op.create_index(name, table, columns, postgresql_concurrently=True, if_not_exists=True)


def downgrade():
    with op.get_context().autocommit_block():
        for name, table, _ in reversed(INDEXES):
            op.drop_index(name, table_name=table, postgresql_concurrently=True, if_exists=True)
