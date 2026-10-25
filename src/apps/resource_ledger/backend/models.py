"""Resource ledger tables: locations, planning levels, prefixes, allocations, and the
business systems, subsystems, hosts and service ports that use them.

Addresses are stored as integers next to their text so containment and ordering are
plain comparisons. A prefix's parent is never stored: CIDR blocks either nest or are
disjoint, so the tree is computed from the address ranges of one routing domain
(``Space``). Each block binds at most one object of its planning level and inherits
the others from the blocks above it.
"""

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()
T = "app_resource_ledger_"


class Region(Base):
    __tablename__ = T + "regions"
    id = Column(String(32), primary_key=True)
    name = Column(String(64), nullable=False, unique=True)
    note = Column(String(500))


class Site(Base):
    __tablename__ = T + "sites"
    id = Column(String(32), primary_key=True)
    region_id = Column(String(32), ForeignKey(Region.id), nullable=False, index=True)
    name = Column(String(64), nullable=False, unique=True)
    kind = Column(String(20), nullable=False)
    address = Column(String(200))
    note = Column(String(500))
    # Core sites carry traffic between others; every other site is checked for its paths to them.
    core = Column(Boolean, nullable=False, default=False)


class Space(Base):
    __tablename__ = T + "spaces"
    id = Column(String(32), primary_key=True)
    name = Column(String(64), nullable=False, unique=True)
    kind = Column(String(20), nullable=False)
    note = Column(String(500))


class Level(Base):
    """One rung of the planning hierarchy; ``dimension`` says what a block of it binds."""

    __tablename__ = T + "levels"
    id = Column(String(32), primary_key=True)
    position = Column(Integer, nullable=False, unique=True)
    dimension = Column(String(12), nullable=False)
    title = Column(String(32), nullable=False)
    default_length = Column(Integer)


class System(Base):
    __tablename__ = T + "systems"
    id = Column(String(32), primary_key=True)
    name = Column(String(64), nullable=False, unique=True)
    short_name = Column(String(32))
    importance = Column(String(10), nullable=False)
    department = Column(String(64))
    business_owner = Column(String(64))
    ops_owner = Column(String(64))
    contact = Column(String(64))
    note = Column(String(500))


class Subsystem(Base):
    __tablename__ = T + "subsystems"
    id = Column(String(32), primary_key=True)
    system_id = Column(String(32), ForeignKey(System.id), nullable=False, index=True)
    name = Column(String(64), nullable=False)
    owner = Column(String(64))
    note = Column(String(500))
    __table_args__ = (UniqueConstraint("system_id", "name", name="uq_resource_ledger_subsystem"),)


class Zone(Base):
    __tablename__ = T + "zones"
    id = Column(String(32), primary_key=True)
    site_id = Column(String(32), ForeignKey(Site.id), nullable=False, index=True)
    name = Column(String(64), nullable=False)
    title = Column(String(64))
    kind = Column(String(20), nullable=False)
    security_level = Column(String(20))
    note = Column(String(500))
    __table_args__ = (UniqueConstraint("site_id", "name", name="uq_resource_ledger_zone"),)


class Prefix(Base):
    __tablename__ = T + "prefixes"
    id = Column(String(32), primary_key=True)
    space_id = Column(String(32), ForeignKey(Space.id), nullable=False)
    network = Column(String(18), nullable=False)
    first = Column(BigInteger, nullable=False)
    last = Column(BigInteger, nullable=False)
    length = Column(Integer, nullable=False)
    purpose = Column(String(200))
    level_id = Column(String(32), ForeignKey(Level.id))
    region_id = Column(String(32), ForeignKey(Region.id))
    site_id = Column(String(32), ForeignKey(Site.id))
    zone_id = Column(String(32), ForeignKey(Zone.id), index=True)
    system_id = Column(String(32), ForeignKey(System.id))
    subsystem_id = Column(String(32), ForeignKey(Subsystem.id))
    label = Column(String(64))
    vlan = Column(Integer)
    gateway = Column(String(15))
    status = Column(String(10), nullable=False)
    owner = Column(String(64))
    ticket = Column(String(80))
    note = Column(String(500))
    updated_at = Column(DateTime(timezone=True), nullable=False)
    updated_by = Column(String(64), nullable=False)
    __table_args__ = (
        UniqueConstraint("space_id", "first", "length", name="uq_resource_ledger_prefix"),
        Index("ix_resource_ledger_prefix_range", "space_id", "first", "last"),
    )


class Reservation(Base):
    __tablename__ = T + "reservations"
    id = Column(String(32), primary_key=True)
    space_id = Column(String(32), ForeignKey(Space.id), nullable=False)
    first = Column(BigInteger, nullable=False)
    last = Column(BigInteger, nullable=False)
    purpose = Column(String(200), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    created_by = Column(String(64), nullable=False)
    __table_args__ = (Index("ix_resource_ledger_reservation_range", "space_id", "first", "last"),)


class Host(Base):
    __tablename__ = T + "hosts"
    id = Column(String(32), primary_key=True)
    hostname = Column(String(128), nullable=False)
    system_id = Column(String(32), ForeignKey(System.id), nullable=False, index=True)
    subsystem_id = Column(String(32), ForeignKey(Subsystem.id), index=True)
    environment = Column(String(10), nullable=False)
    status = Column(String(10), nullable=False)
    kind = Column(String(10), nullable=False)
    role = Column(String(10), nullable=False)
    os = Column(String(64))
    owner = Column(String(64))
    online_date = Column(String(10))
    offline_date = Column(String(10))
    tags = Column(String(200))
    note = Column(String(500))
    updated_at = Column(DateTime(timezone=True), nullable=False)
    updated_by = Column(String(64), nullable=False)


class Service(Base):
    """A port a host serves; ``port_first``/``port_last`` are empty for ICMP."""

    __tablename__ = T + "services"
    id = Column(String(32), primary_key=True)
    host_id = Column(String(32), ForeignKey(Host.id), nullable=False, index=True)
    protocol = Column(String(4), nullable=False)
    port_first = Column(Integer)
    port_last = Column(Integer)
    name = Column(String(64))
    note = Column(String(200))


class Address(Base):
    __tablename__ = T + "addresses"
    id = Column(String(32), primary_key=True)
    space_id = Column(String(32), ForeignKey(Space.id), nullable=False)
    value = Column(BigInteger, nullable=False)
    ip = Column(String(15), nullable=False)
    kind = Column(String(12), nullable=False)
    hostname = Column(String(128))
    dns_name = Column(String(255))
    note = Column(String(500))
    assigned_at = Column(DateTime(timezone=True), nullable=False)
    assigned_by = Column(String(64), nullable=False)
    host_id = Column(String(32), ForeignKey(Host.id), index=True)
    __table_args__ = (UniqueConstraint("space_id", "value", name="uq_resource_ledger_address"),)


class Provider(Base):
    __tablename__ = T + "providers"
    id = Column(String(32), primary_key=True)
    name = Column(String(64), nullable=False, unique=True)
    support_phone = Column(String(80))
    account = Column(String(80))
    contact = Column(String(120))
    note = Column(String(500))


class ProviderNetwork(Base):
    """A provider cloud circuits terminate into, e.g. an MPLS VPN or the internet."""

    __tablename__ = T + "provider_networks"
    id = Column(String(32), primary_key=True)
    provider_id = Column(String(32), ForeignKey(Provider.id), nullable=False, index=True)
    name = Column(String(80), nullable=False)
    kind = Column(String(12), nullable=False)
    service_id = Column(String(80))
    # Whether sites reach each other through it (MPLS VPN yes; the internet only with an overlay).
    interconnects = Column(Boolean, nullable=False, default=True)
    note = Column(String(500))
    __table_args__ = (UniqueConstraint("provider_id", "name", name="uq_resource_ledger_provider_network"),)


class Pop(Base):
    """A provider's point of presence a circuit enters, e.g. an exchange or PE."""

    __tablename__ = T + "pops"
    id = Column(String(32), primary_key=True)
    provider_id = Column(String(32), ForeignKey(Provider.id), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    note = Column(String(500))
    __table_args__ = (UniqueConstraint("provider_id", "name", name="uq_resource_ledger_pop"),)


class CircuitType(Base):
    __tablename__ = T + "circuit_types"
    id = Column(String(32), primary_key=True)
    name = Column(String(40), nullable=False, unique=True)
    note = Column(String(200))


class RiskGroup(Base):
    """A shared physical risk, such as one fibre cable or duct several circuits run through."""

    __tablename__ = T + "risk_groups"
    id = Column(String(32), primary_key=True)
    name = Column(String(80), nullable=False, unique=True)
    kind = Column(String(10), nullable=False)
    note = Column(String(500))


class Circuit(Base):
    __tablename__ = T + "circuits"
    id = Column(String(32), primary_key=True)
    provider_id = Column(String(32), ForeignKey(Provider.id), nullable=False, index=True)
    cid = Column(String(80), nullable=False)
    type_id = Column(String(32), ForeignKey(CircuitType.id), nullable=False, index=True)
    status = Column(String(10), nullable=False)
    bandwidth = Column(Integer)
    install_date = Column(String(10))
    description = Column(String(200))
    prefix_id = Column(String(32), ForeignKey(Prefix.id), index=True)
    vlan = Column(Integer)
    routing = Column(String(10))
    local_as = Column(String(20))
    remote_as = Column(String(20))
    note = Column(String(500))
    updated_at = Column(DateTime(timezone=True), nullable=False)
    updated_by = Column(String(64), nullable=False)
    __table_args__ = (UniqueConstraint("provider_id", "cid", name="uq_resource_ledger_circuit"),)


class Termination(Base):
    """One end of a circuit: a site of ours, or a provider network."""

    __tablename__ = T + "circuit_terminations"
    id = Column(String(32), primary_key=True)
    circuit_id = Column(String(32), ForeignKey(Circuit.id), nullable=False, index=True)
    side = Column(String(1), nullable=False)
    site_id = Column(String(32), ForeignKey(Site.id), index=True)
    network_id = Column(String(32), ForeignKey(ProviderNetwork.id), index=True)
    device = Column(String(80))
    interface = Column(String(80))
    port_speed = Column(Integer)
    xconnect = Column(String(120))
    pop_id = Column(String(32), ForeignKey(Pop.id), index=True)
    ip = Column(String(15))
    address_id = Column(String(32), ForeignKey(Address.id), index=True)
    note = Column(String(200))
    __table_args__ = (UniqueConstraint("circuit_id", "side", name="uq_resource_ledger_termination"),)


class CircuitRisk(Base):
    __tablename__ = T + "circuit_risks"
    circuit_id = Column(String(32), ForeignKey(Circuit.id), primary_key=True)
    risk_id = Column(String(32), ForeignKey(RiskGroup.id), primary_key=True, index=True)


class Change(Base):
    __tablename__ = T + "changes"
    id = Column(String(32), primary_key=True)
    at = Column(DateTime(timezone=True), nullable=False, index=True)
    actor_id = Column(String(64), nullable=False)
    actor_name = Column(String(150), nullable=False)
    entity = Column(String(20), nullable=False)
    entity_id = Column(String(32), nullable=False, index=True)
    label = Column(String(200), nullable=False)
    action = Column(String(10), nullable=False)
    detail = Column(JSON)


TABLES = [model.__tablename__ for model in (
    Region, Site, Space, Level, System, Subsystem, Zone, Prefix, Reservation, Host, Service, Address,
    Provider, ProviderNetwork, Pop, CircuitType, RiskGroup, Circuit, Termination, CircuitRisk, Change)]
