"""The active topology: at most one network per owner, the one the assistant reads.

Each owner's network with the most recent current snapshot becomes active, so the
assistant keeps answering after the upgrade; an owner without snapshots gets none.
"""

import sqlalchemy as sa
from alembic import op

revision = "topology_0005"
down_revision = "topology_0004"


def upgrade():
    op.add_column(
        "app_topology_networks",
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_index(
        "uq_topology_active_network",
        "app_topology_networks",
        ["owner_id"],
        unique=True,
        postgresql_where=sa.text("active"),
        sqlite_where=sa.text("active"),
    )
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT n.id, n.owner_id, s.created_at FROM app_topology_networks n "
            "JOIN app_topology_snapshots s ON s.id = n.current_snapshot"
        )
    ).fetchall()
    latest = {}
    for network_id, owner_id, created_at in rows:
        if owner_id not in latest or created_at > latest[owner_id][1]:
            latest[owner_id] = (network_id, created_at)
    for network_id, _ in latest.values():
        bind.execute(
            sa.text("UPDATE app_topology_networks SET active = true WHERE id = :id"),
            {"id": network_id},
        )


def downgrade():
    raise RuntimeError("Restore a verified backup instead of dropping the active topology")
