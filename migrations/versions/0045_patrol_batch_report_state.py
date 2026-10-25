"""Add patrol batch report failure detail.

Revision ID: 0045_patrol_batch_report_state
Revises: 0044_patrol_batch_dispatch
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0045_patrol_batch_report_state"
down_revision = "0044_patrol_batch_dispatch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("netops_patrol_batches", sa.Column("report_error", sa.Text()))


def downgrade() -> None:
    op.drop_column("netops_patrol_batches", "report_error")
