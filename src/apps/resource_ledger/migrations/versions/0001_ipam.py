"""IP address management: locations, address spaces, zones, prefixes, allocations, changes."""

import sqlalchemy as sa
from alembic import op

revision = "resource_ledger_0001"
down_revision = None

T = "app_resource_ledger_"


def upgrade():
    op.create_table(
        T + "regions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("note", sa.String(500)),
    )
    op.create_table(
        T + "sites",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("region_id", sa.String(32), sa.ForeignKey(T + "regions.id"), nullable=False, index=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("address", sa.String(200)),
        sa.Column("note", sa.String(500)),
    )
    op.create_table(
        T + "spaces",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("note", sa.String(500)),
    )
    op.create_table(
        T + "zones",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("site_id", sa.String(32), sa.ForeignKey(T + "sites.id"), nullable=False, index=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("title", sa.String(64)),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("security_level", sa.String(20)),
        sa.Column("firewall", sa.String(80)),
        sa.Column("security_domain", sa.String(80)),
        sa.Column("note", sa.String(500)),
        sa.UniqueConstraint("site_id", "name", name="uq_resource_ledger_zone"),
    )
    op.create_table(
        T + "prefixes",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("space_id", sa.String(32), sa.ForeignKey(T + "spaces.id"), nullable=False),
        sa.Column("network", sa.String(18), nullable=False),
        sa.Column("first", sa.BigInteger, nullable=False),
        sa.Column("last", sa.BigInteger, nullable=False),
        sa.Column("length", sa.Integer, nullable=False),
        sa.Column("purpose", sa.String(200), nullable=False),
        sa.Column("zone_id", sa.String(32), sa.ForeignKey(T + "zones.id"), index=True),
        sa.Column("vlan", sa.Integer),
        sa.Column("gateway", sa.String(15)),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("owner", sa.String(64)),
        sa.Column("ticket", sa.String(80)),
        sa.Column("note", sa.String(500)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(64), nullable=False),
        sa.UniqueConstraint("space_id", "first", "length", name="uq_resource_ledger_prefix"),
    )
    op.create_index("ix_resource_ledger_prefix_range", T + "prefixes", ["space_id", "first", "last"])
    op.create_table(
        T + "reservations",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("space_id", sa.String(32), sa.ForeignKey(T + "spaces.id"), nullable=False),
        sa.Column("first", sa.BigInteger, nullable=False),
        sa.Column("last", sa.BigInteger, nullable=False),
        sa.Column("purpose", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
    )
    op.create_index(
        "ix_resource_ledger_reservation_range", T + "reservations", ["space_id", "first", "last"]
    )
    op.create_table(
        T + "addresses",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("space_id", sa.String(32), sa.ForeignKey(T + "spaces.id"), nullable=False),
        sa.Column("value", sa.BigInteger, nullable=False),
        sa.Column("ip", sa.String(15), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("hostname", sa.String(128)),
        sa.Column("dns_name", sa.String(255)),
        sa.Column("note", sa.String(500)),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("assigned_by", sa.String(64), nullable=False),
        sa.UniqueConstraint("space_id", "value", name="uq_resource_ledger_address"),
    )
    op.create_table(
        T + "changes",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("actor_id", sa.String(64), nullable=False),
        sa.Column("actor_name", sa.String(150), nullable=False),
        sa.Column("entity", sa.String(20), nullable=False),
        sa.Column("entity_id", sa.String(32), nullable=False, index=True),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("detail", sa.JSON),
    )


def downgrade():
    raise RuntimeError("Restore a verified backup instead of deleting business data")
