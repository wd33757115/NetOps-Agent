"""Hierarchical planning and the application ledger.

Prefixes carve down from address resources through planning levels (region, site, zone,
system, subsystem); each block binds one object of its level and inherits the rest.
Business systems, subsystems, hosts and their service ports join the ledger, and an
allocated address can belong to a host.
"""

import sqlalchemy as sa
from alembic import op

revision = "resource_ledger_0002"
down_revision = "resource_ledger_0001"

T = "app_resource_ledger_"


# The hierarchy most networks are planned by; planners can rename, reorder or extend it.
DEFAULT_LEVELS = [
    {"id": "lv-region", "position": 1, "dimension": "region", "title": "地域", "default_length": None},
    {"id": "lv-site", "position": 2, "dimension": "site", "title": "机房", "default_length": None},
    {"id": "lv-zone", "position": 3, "dimension": "zone", "title": "网络区域", "default_length": None},
    {"id": "lv-system", "position": 4, "dimension": "system", "title": "业务系统", "default_length": 22},
    {"id": "lv-subsystem", "position": 5, "dimension": "subsystem", "title": "子系统", "default_length": 24},
]


def upgrade():
    levels = op.create_table(
        T + "levels",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("position", sa.Integer, nullable=False, unique=True),
        sa.Column("dimension", sa.String(12), nullable=False),
        sa.Column("title", sa.String(32), nullable=False),
        sa.Column("default_length", sa.Integer),
    )
    op.bulk_insert(levels, DEFAULT_LEVELS)
    op.create_table(
        T + "systems",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("short_name", sa.String(32)),
        sa.Column("importance", sa.String(10), nullable=False),
        sa.Column("department", sa.String(64)),
        sa.Column("business_owner", sa.String(64)),
        sa.Column("ops_owner", sa.String(64)),
        sa.Column("contact", sa.String(64)),
        sa.Column("note", sa.String(500)),
    )
    op.create_table(
        T + "subsystems",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("system_id", sa.String(32), sa.ForeignKey(T + "systems.id"), nullable=False, index=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("owner", sa.String(64)),
        sa.Column("note", sa.String(500)),
        sa.UniqueConstraint("system_id", "name", name="uq_resource_ledger_subsystem"),
    )
    op.create_table(
        T + "hosts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("hostname", sa.String(128), nullable=False),
        sa.Column("system_id", sa.String(32), sa.ForeignKey(T + "systems.id"), nullable=False, index=True),
        sa.Column("subsystem_id", sa.String(32), sa.ForeignKey(T + "subsystems.id"), index=True),
        sa.Column("environment", sa.String(10), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column("os", sa.String(64)),
        sa.Column("owner", sa.String(64)),
        sa.Column("online_date", sa.String(10)),
        sa.Column("offline_date", sa.String(10)),
        sa.Column("tags", sa.String(200)),
        sa.Column("note", sa.String(500)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(64), nullable=False),
    )
    op.create_table(
        T + "services",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("host_id", sa.String(32), sa.ForeignKey(T + "hosts.id"), nullable=False, index=True),
        sa.Column("protocol", sa.String(4), nullable=False),
        sa.Column("port_first", sa.Integer),
        sa.Column("port_last", sa.Integer),
        sa.Column("name", sa.String(64)),
        sa.Column("note", sa.String(200)),
    )
    with op.batch_alter_table(T + "prefixes") as table:
        table.alter_column("purpose", existing_type=sa.String(200), nullable=True)
        table.add_column(sa.Column("level_id", sa.String(32), sa.ForeignKey(T + "levels.id", name="fk_resource_ledger_prefix_level")))
        table.add_column(sa.Column("region_id", sa.String(32), sa.ForeignKey(T + "regions.id", name="fk_resource_ledger_prefix_region")))
        table.add_column(sa.Column("site_id", sa.String(32), sa.ForeignKey(T + "sites.id", name="fk_resource_ledger_prefix_site")))
        table.add_column(sa.Column("system_id", sa.String(32), sa.ForeignKey(T + "systems.id", name="fk_resource_ledger_prefix_system")))
        table.add_column(sa.Column("subsystem_id", sa.String(32), sa.ForeignKey(T + "subsystems.id", name="fk_resource_ledger_prefix_subsystem")))
        table.add_column(sa.Column("label", sa.String(64)))
    with op.batch_alter_table(T + "addresses") as table:
        table.add_column(sa.Column("host_id", sa.String(32), sa.ForeignKey(T + "hosts.id", name="fk_resource_ledger_address_host")))
        table.create_index("ix_resource_ledger_address_host", ["host_id"])
    # Most networks have one routing domain; give it a name so nobody has to create it.
    if not op.get_bind().execute(sa.text(f"SELECT COUNT(*) FROM {T}spaces")).scalar():
        op.bulk_insert(
            sa.table(T + "spaces", sa.column("id"), sa.column("name"), sa.column("kind"), sa.column("note")),
            [{"id": "space-global", "name": "全局", "kind": "其他", "note": "默认路由域"}],
        )


def downgrade():
    raise RuntimeError("Restore a verified backup instead of deleting business data")
