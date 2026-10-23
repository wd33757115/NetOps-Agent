"""Carrier circuits: providers, their networks and points of presence, circuit types,
shared physical risks, circuits with their A/Z terminations, and what sites require.

Only technical facts are kept; contracts and costs stay outside the ledger.
"""

import sqlalchemy as sa
from alembic import op

revision = "resource_ledger_0003"
down_revision = "resource_ledger_0002"

T = "app_resource_ledger_"

# Common kinds; planners add or rename their own.
DEFAULT_TYPES = [
    ("ct-mpls", "MPLS VPN"), ("ct-mstp", "MSTP/SDH"), ("ct-fiber", "裸光纤/波分"), ("ct-internet", "互联网专线"),
    ("ct-wireless", "4G/5G 备份"), ("ct-cloud", "云专线"), ("ct-intl", "国际专线"), ("ct-other", "其他"),
]


def upgrade():
    with op.batch_alter_table(T + "sites") as table:
        table.add_column(sa.Column("core", sa.Boolean, nullable=False, server_default=sa.false()))
    op.create_table(
        T + "providers",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("support_phone", sa.String(80)),
        sa.Column("account", sa.String(80)),
        sa.Column("contact", sa.String(120)),
        sa.Column("note", sa.String(500)),
    )
    op.create_table(
        T + "provider_networks",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("provider_id", sa.String(32), sa.ForeignKey(T + "providers.id"), nullable=False, index=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("service_id", sa.String(80)),
        sa.Column("interconnects", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("note", sa.String(500)),
        sa.UniqueConstraint("provider_id", "name", name="uq_resource_ledger_provider_network"),
    )
    op.create_table(
        T + "pops",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("provider_id", sa.String(32), sa.ForeignKey(T + "providers.id"), nullable=False, index=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("note", sa.String(500)),
        sa.UniqueConstraint("provider_id", "name", name="uq_resource_ledger_pop"),
    )
    types = op.create_table(
        T + "circuit_types",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(40), nullable=False, unique=True),
        sa.Column("note", sa.String(200)),
    )
    op.bulk_insert(types, [{"id": ident, "name": name, "note": None} for ident, name in DEFAULT_TYPES])
    op.create_table(
        T + "risk_groups",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False, unique=True),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("note", sa.String(500)),
    )
    op.create_table(
        T + "circuits",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("provider_id", sa.String(32), sa.ForeignKey(T + "providers.id"), nullable=False, index=True),
        sa.Column("cid", sa.String(80), nullable=False),
        sa.Column("type_id", sa.String(32), sa.ForeignKey(T + "circuit_types.id"), nullable=False, index=True),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("bandwidth", sa.Integer),
        sa.Column("install_date", sa.String(10)),
        sa.Column("description", sa.String(200)),
        sa.Column("prefix_id", sa.String(32), sa.ForeignKey(T + "prefixes.id"), index=True),
        sa.Column("vlan", sa.Integer),
        sa.Column("routing", sa.String(10)),
        sa.Column("local_as", sa.String(20)),
        sa.Column("remote_as", sa.String(20)),
        sa.Column("note", sa.String(500)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(64), nullable=False),
        sa.UniqueConstraint("provider_id", "cid", name="uq_resource_ledger_circuit"),
    )
    op.create_table(
        T + "circuit_terminations",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("circuit_id", sa.String(32), sa.ForeignKey(T + "circuits.id"), nullable=False, index=True),
        sa.Column("side", sa.String(1), nullable=False),
        sa.Column("site_id", sa.String(32), sa.ForeignKey(T + "sites.id"), index=True),
        sa.Column("network_id", sa.String(32), sa.ForeignKey(T + "provider_networks.id"), index=True),
        sa.Column("device", sa.String(80)),
        sa.Column("interface", sa.String(80)),
        sa.Column("port_speed", sa.Integer),
        sa.Column("xconnect", sa.String(120)),
        sa.Column("pop_id", sa.String(32), sa.ForeignKey(T + "pops.id"), index=True),
        sa.Column("ip", sa.String(15)),
        sa.Column("address_id", sa.String(32), sa.ForeignKey(T + "addresses.id"), index=True),
        sa.Column("note", sa.String(200)),
        sa.UniqueConstraint("circuit_id", "side", name="uq_resource_ledger_termination"),
    )
    op.create_table(
        T + "circuit_risks",
        sa.Column("circuit_id", sa.String(32), sa.ForeignKey(T + "circuits.id"), primary_key=True),
        sa.Column("risk_id", sa.String(32), sa.ForeignKey(T + "risk_groups.id"), primary_key=True, index=True),
    )


def downgrade():
    raise RuntimeError("Restore a verified backup instead of deleting business data")
