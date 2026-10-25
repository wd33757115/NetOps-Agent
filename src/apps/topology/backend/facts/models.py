"""Domain-scoped identities and immutable, capability-scoped observations."""

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from ..models import Base, now


class Device(Base):
    __tablename__ = "app_twin_devices"
    device_uid = Column(String(32), primary_key=True)
    domain = Column(String(64), nullable=False, index=True)
    seed_address = Column(String(45))  # Weak candidate key; clear on strong identity resolution.
    kind = Column(String(32), nullable=False)
    asset_ref = Column(String(128))
    hostname = Column(String(256))
    vendor = Column(String(64))
    platform = Column(String(64))
    model = Column(String(128))
    os_version = Column(String(256))
    serials = Column(JSON, nullable=False)
    chassis_macs = Column(JSON, nullable=False)
    mgmt_addresses = Column(JSON, nullable=False)
    role = Column(String(32), nullable=False)
    identity_confidence = Column(String(16), nullable=False)
    created_by = Column(String(64), nullable=False)
    first_seen = Column(DateTime(timezone=True), nullable=False, default=now)
    last_seen = Column(DateTime(timezone=True), nullable=False, default=now)
    __table_args__ = (UniqueConstraint("domain", "seed_address", name="uq_twin_seed"),)


class FactSet(Base):
    __tablename__ = "app_twin_fact_sets"
    id = Column(String(32), primary_key=True)
    device_uid = Column(String(32), ForeignKey(Device.device_uid), nullable=False)
    capability = Column(String(32), nullable=False)
    run_id = Column(String(32), ForeignKey("app_topology_runs.id"))
    source = Column(String(16), nullable=False)
    observed_at = Column(DateTime(timezone=True), nullable=False)
    received_at = Column(DateTime(timezone=True), nullable=False, default=now)
    status = Column(String(16), nullable=False)
    platform_profile = Column(String(128), nullable=False)
    parser_ref = Column(String(512), nullable=False)
    evidence_ref = Column(String(512), nullable=False)
    evidence_sha256 = Column(String(64), nullable=False)
    record_count = Column(Integer, nullable=False)
    collected_by = Column(String(64), nullable=False)
    # Version/inventory/local identity and empty unsupported capabilities still have evidence.
    summary = Column(JSON, nullable=False)
    __table_args__ = (
        UniqueConstraint("id", "device_uid", name="uq_twin_fact_device"),
        Index("ix_twin_fact_current", "device_uid", "capability", observed_at.desc()),
    )


class DeviceMember(Base):
    __tablename__ = "app_twin_device_members"
    id = Column(String(32), primary_key=True)
    device_uid = Column(String(32), ForeignKey(Device.device_uid), nullable=False)
    fact_set_id = Column(String(32), ForeignKey(FactSet.id), nullable=False)
    member_id = Column(String(64), nullable=False)
    serial = Column(String(256))
    role = Column(String(32))
    __table_args__ = (
        ForeignKeyConstraint(
            ["fact_set_id", "device_uid"],
            ["app_twin_fact_sets.id", "app_twin_fact_sets.device_uid"],
        ),
    )


class IdentityAlias(Base):
    __tablename__ = "app_twin_identity_aliases"
    id = Column(String(32), primary_key=True)
    device_uid = Column(String(32), ForeignKey(Device.device_uid), nullable=False)
    kind = Column(String(32), nullable=False)
    value = Column(String(256), nullable=False)
    status = Column(String(16), nullable=False)
    __table_args__ = (
        UniqueConstraint("device_uid", "kind", "value", name="uq_twin_alias"),
        Index("ix_twin_alias_lookup", "kind", "value"),
    )


def fact_model(name, suffix, columns, indexes=()):
    """Every row belongs to exactly one device and one fact set, enforced by FK."""
    return type(
        name,
        (Base,),
        {
            "__tablename__": "app_twin_" + suffix,
            "id": Column(String(32), primary_key=True),
            "device_uid": Column(String(32), nullable=False),
            "fact_set_id": Column(String(32), nullable=False),
            **columns,
            "__table_args__": (
                ForeignKeyConstraint(
                    ["fact_set_id", "device_uid"],
                    ["app_twin_fact_sets.id", "app_twin_fact_sets.device_uid"],
                ),
                Index("ix_twin_" + suffix + "_set", "fact_set_id"),
                *indexes,
            ),
        },
    )


def string(length=256, *, required=False):
    return Column(String(length), nullable=not required)


Interface = fact_model(
    "Interface",
    "interfaces",
    {
        "ifname": string(required=True),
        "raw_name": string(required=True),
        "type": string(32),
        "admin_status": string(32),
        "oper_status": string(32),
        "speed_mbps": Column(Integer),
        "mtu": Column(Integer),
        "mac": string(17),
        "description": Column(Text),
        "lag_parent": string(),
        "l2_mode": string(32),
        "access_vlan": Column(Integer),
        "trunk_vlans": Column(Text),
        "vrf": string(),
    },
)
IPAddress = fact_model(
    "IPAddress",
    "ip_addresses",
    {
        "ifname": string(required=True),
        "vrf": string(required=True),
        "address": string(45, required=True),
        "prefix_len": Column(Integer, nullable=False),
        "kind": string(32),
    },
    (Index("ix_twin_ip_address", "address"),),
)
ARP = fact_model(
    "ARP",
    "arp",
    {
        "vrf": string(required=True),
        "ip": string(45, required=True),
        "mac": string(17, required=True),
        "ifname": string(),
        "vlan": Column(Integer),
        "type": string(32),
        "age_s": Column(Integer),
    },
    (Index("ix_twin_arp_ip", "ip"),),
)
MAC = fact_model(
    "MAC",
    "mac",
    {
        "vlan_or_bd": string(64, required=True),
        "mac": string(17, required=True),
        "port": string(required=True),
        "type": string(32),
        "remote_vtep": string(45),
    },
    (Index("ix_twin_mac_mac", "mac"),),
)
LLDP = fact_model(
    "LLDP",
    "lldp",
    {
        "local_if": string(required=True),
        "remote_chassis_id": string(required=True),
        "remote_chassis_subtype": string(32),
        "remote_port_id": string(required=True),
        "remote_port_subtype": string(32),
        "remote_sysname": string(),
        "remote_mgmt_addresses": Column(JSON, nullable=False),
        "remote_system_description": Column(Text),
    },
)
Route = fact_model(
    "Route",
    "routes",
    {
        "vrf": string(required=True),
        "prefix": string(49, required=True),
        "protocol": string(32),
        "preference": Column(Integer),
        "metric": Column(Integer),
        "next_hops": Column(JSON, nullable=False),
    },
    (Index("ix_twin_routes_device_vrf", "device_uid", "vrf"),),
)
VLAN = fact_model(
    "VLAN",
    "vlans",
    {
        "vlan_id": Column(Integer, nullable=False),
        "name": string(),
        "vni": Column(Integer),
    },
)

RECORD_MODELS = {
    "interfaces": Interface,
    "ip": IPAddress,
    "arp": ARP,
    "mac": MAC,
    "lldp": LLDP,
    "routes": Route,
    "vlan": VLAN,
    "inventory": DeviceMember,
}
