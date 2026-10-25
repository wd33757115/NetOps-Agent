"""Collection issues: what a node could not give over SSH or NETCONF, for a human to fix."""

import sqlalchemy as sa
from alembic import op

revision = "topology_0004"
down_revision = "topology_0003"


def upgrade():
    op.create_table(
        "app_topology_collection_issues",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner_id", sa.String(64), nullable=False, index=True),
        sa.Column("network_id", sa.String(32), nullable=False, index=True),
        sa.Column("address", sa.String(64), nullable=False),
        sa.Column("device", sa.String(255)),
        sa.Column("capability", sa.String(64), nullable=False),
        sa.Column("transport", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(128), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_run_id", sa.String(32), nullable=False),
        sa.Column("last_observation_id", sa.String(32)),
        sa.Column("cleared_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "owner_id", "network_id", "address", "capability", "transport", "kind"
        ),
    )


def downgrade():
    raise RuntimeError("Restore a verified backup instead of deleting collection issues")
