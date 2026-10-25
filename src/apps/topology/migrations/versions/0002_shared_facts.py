"""Shared domain facts. Frozen schema; no imports from live App models."""

import sqlalchemy as sa
from alembic import op

revision = "topology_0002"
down_revision = "topology_0001"


def upgrade():
    op.create_table(
        "app_twin_devices",
        sa.Column("device_uid", sa.String(length=32), nullable=False, primary_key=True),
        sa.Column("domain", sa.String(length=64), nullable=False),
        sa.Column("seed_address", sa.String(length=45), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("asset_ref", sa.String(length=128), nullable=True),
        sa.Column("hostname", sa.String(length=256), nullable=True),
        sa.Column("vendor", sa.String(length=64), nullable=True),
        sa.Column("platform", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("os_version", sa.String(length=256), nullable=True),
        sa.Column("serials", sa.JSON(), nullable=False),
        sa.Column("chassis_macs", sa.JSON(), nullable=False),
        sa.Column("mgmt_addresses", sa.JSON(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("identity_confidence", sa.String(length=16), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(*["domain", "seed_address"], name="uq_twin_seed"),
    )
    op.create_index("ix_app_twin_devices_domain", "app_twin_devices", ["domain"])
    op.create_table(
        "app_twin_identity_aliases",
        sa.Column("id", sa.String(length=32), nullable=False, primary_key=True),
        sa.Column("device_uid", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("value", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(["device_uid"], ["app_twin_devices.device_uid"]),
        sa.UniqueConstraint(*["device_uid", "kind", "value"], name="uq_twin_alias"),
    )
    op.create_index("ix_twin_alias_lookup", "app_twin_identity_aliases", ["kind", "value"])
    op.create_table(
        "app_twin_fact_sets",
        sa.Column("id", sa.String(length=32), nullable=False, primary_key=True),
        sa.Column("device_uid", sa.String(length=32), nullable=False),
        sa.Column("capability", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=32), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("platform_profile", sa.String(length=128), nullable=False),
        sa.Column("parser_ref", sa.String(length=512), nullable=False),
        sa.Column("evidence_ref", sa.String(length=512), nullable=False),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("collected_by", sa.String(length=64), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["app_topology_runs.id"]),
        sa.ForeignKeyConstraint(["device_uid"], ["app_twin_devices.device_uid"]),
        sa.UniqueConstraint(*["id", "device_uid"], name="uq_twin_fact_device"),
    )
    op.create_index(
        "ix_twin_fact_current",
        "app_twin_fact_sets",
        ["device_uid", "capability", sa.text("observed_at DESC")],
    )
    op.create_table(
        "app_twin_arp",
        sa.Column("id", sa.String(length=32), nullable=False, primary_key=True),
        sa.Column("device_uid", sa.String(length=32), nullable=False),
        sa.Column("fact_set_id", sa.String(length=32), nullable=False),
        sa.Column("vrf", sa.String(length=256), nullable=False),
        sa.Column("ip", sa.String(length=45), nullable=False),
        sa.Column("mac", sa.String(length=17), nullable=False),
        sa.Column("ifname", sa.String(length=256), nullable=True),
        sa.Column("vlan", sa.Integer(), nullable=True),
        sa.Column("type", sa.String(length=32), nullable=True),
        sa.Column("age_s", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["fact_set_id", "device_uid"],
            ["app_twin_fact_sets.id", "app_twin_fact_sets.device_uid"],
        ),
    )
    op.create_index("ix_twin_arp_ip", "app_twin_arp", ["ip"])
    op.create_index("ix_twin_arp_set", "app_twin_arp", ["fact_set_id"])
    op.create_table(
        "app_twin_device_members",
        sa.Column("id", sa.String(length=32), nullable=False, primary_key=True),
        sa.Column("device_uid", sa.String(length=32), nullable=False),
        sa.Column("fact_set_id", sa.String(length=32), nullable=False),
        sa.Column("member_id", sa.String(length=64), nullable=False),
        sa.Column("serial", sa.String(length=256), nullable=True),
        sa.Column("role", sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(["device_uid"], ["app_twin_devices.device_uid"]),
        sa.ForeignKeyConstraint(["fact_set_id"], ["app_twin_fact_sets.id"]),
        sa.ForeignKeyConstraint(
            ["fact_set_id", "device_uid"],
            ["app_twin_fact_sets.id", "app_twin_fact_sets.device_uid"],
        ),
    )
    op.create_table(
        "app_twin_interfaces",
        sa.Column("id", sa.String(length=32), nullable=False, primary_key=True),
        sa.Column("device_uid", sa.String(length=32), nullable=False),
        sa.Column("fact_set_id", sa.String(length=32), nullable=False),
        sa.Column("ifname", sa.String(length=256), nullable=False),
        sa.Column("raw_name", sa.String(length=256), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=True),
        sa.Column("admin_status", sa.String(length=32), nullable=True),
        sa.Column("oper_status", sa.String(length=32), nullable=True),
        sa.Column("speed_mbps", sa.Integer(), nullable=True),
        sa.Column("mtu", sa.Integer(), nullable=True),
        sa.Column("mac", sa.String(length=17), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("lag_parent", sa.String(length=256), nullable=True),
        sa.Column("l2_mode", sa.String(length=32), nullable=True),
        sa.Column("access_vlan", sa.Integer(), nullable=True),
        sa.Column("trunk_vlans", sa.Text(), nullable=True),
        sa.Column("vrf", sa.String(length=256), nullable=True),
        sa.ForeignKeyConstraint(
            ["fact_set_id", "device_uid"],
            ["app_twin_fact_sets.id", "app_twin_fact_sets.device_uid"],
        ),
    )
    op.create_index("ix_twin_interfaces_set", "app_twin_interfaces", ["fact_set_id"])
    op.create_table(
        "app_twin_ip_addresses",
        sa.Column("id", sa.String(length=32), nullable=False, primary_key=True),
        sa.Column("device_uid", sa.String(length=32), nullable=False),
        sa.Column("fact_set_id", sa.String(length=32), nullable=False),
        sa.Column("ifname", sa.String(length=256), nullable=False),
        sa.Column("vrf", sa.String(length=256), nullable=False),
        sa.Column("address", sa.String(length=45), nullable=False),
        sa.Column("prefix_len", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(
            ["fact_set_id", "device_uid"],
            ["app_twin_fact_sets.id", "app_twin_fact_sets.device_uid"],
        ),
    )
    op.create_index("ix_twin_ip_address", "app_twin_ip_addresses", ["address"])
    op.create_index("ix_twin_ip_addresses_set", "app_twin_ip_addresses", ["fact_set_id"])
    op.create_table(
        "app_twin_lldp",
        sa.Column("id", sa.String(length=32), nullable=False, primary_key=True),
        sa.Column("device_uid", sa.String(length=32), nullable=False),
        sa.Column("fact_set_id", sa.String(length=32), nullable=False),
        sa.Column("local_if", sa.String(length=256), nullable=False),
        sa.Column("remote_chassis_id", sa.String(length=256), nullable=False),
        sa.Column("remote_chassis_subtype", sa.String(length=32), nullable=True),
        sa.Column("remote_port_id", sa.String(length=256), nullable=False),
        sa.Column("remote_port_subtype", sa.String(length=32), nullable=True),
        sa.Column("remote_sysname", sa.String(length=256), nullable=True),
        sa.Column("remote_mgmt_addresses", sa.JSON(), nullable=False),
        sa.Column("remote_system_description", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["fact_set_id", "device_uid"],
            ["app_twin_fact_sets.id", "app_twin_fact_sets.device_uid"],
        ),
    )
    op.create_index("ix_twin_lldp_set", "app_twin_lldp", ["fact_set_id"])
    op.create_table(
        "app_twin_mac",
        sa.Column("id", sa.String(length=32), nullable=False, primary_key=True),
        sa.Column("device_uid", sa.String(length=32), nullable=False),
        sa.Column("fact_set_id", sa.String(length=32), nullable=False),
        sa.Column("vlan_or_bd", sa.String(length=64), nullable=False),
        sa.Column("mac", sa.String(length=17), nullable=False),
        sa.Column("port", sa.String(length=256), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=True),
        sa.Column("remote_vtep", sa.String(length=45), nullable=True),
        sa.ForeignKeyConstraint(
            ["fact_set_id", "device_uid"],
            ["app_twin_fact_sets.id", "app_twin_fact_sets.device_uid"],
        ),
    )
    op.create_index("ix_twin_mac_mac", "app_twin_mac", ["mac"])
    op.create_index("ix_twin_mac_set", "app_twin_mac", ["fact_set_id"])
    op.create_table(
        "app_twin_routes",
        sa.Column("id", sa.String(length=32), nullable=False, primary_key=True),
        sa.Column("device_uid", sa.String(length=32), nullable=False),
        sa.Column("fact_set_id", sa.String(length=32), nullable=False),
        sa.Column("vrf", sa.String(length=256), nullable=False),
        sa.Column("prefix", sa.String(length=49), nullable=False),
        sa.Column("protocol", sa.String(length=32), nullable=True),
        sa.Column("preference", sa.Integer(), nullable=True),
        sa.Column("metric", sa.Integer(), nullable=True),
        sa.Column("next_hops", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["fact_set_id", "device_uid"],
            ["app_twin_fact_sets.id", "app_twin_fact_sets.device_uid"],
        ),
    )
    op.create_index("ix_twin_routes_device_vrf", "app_twin_routes", ["device_uid", "vrf"])
    op.create_index("ix_twin_routes_set", "app_twin_routes", ["fact_set_id"])
    op.create_table(
        "app_twin_vlans",
        sa.Column("id", sa.String(length=32), nullable=False, primary_key=True),
        sa.Column("device_uid", sa.String(length=32), nullable=False),
        sa.Column("fact_set_id", sa.String(length=32), nullable=False),
        sa.Column("vlan_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=True),
        sa.Column("vni", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["fact_set_id", "device_uid"],
            ["app_twin_fact_sets.id", "app_twin_fact_sets.device_uid"],
        ),
    )
    op.create_index("ix_twin_vlans_set", "app_twin_vlans", ["fact_set_id"])


def downgrade():
    raise RuntimeError("Restore a verified backup instead of deleting shared facts")
