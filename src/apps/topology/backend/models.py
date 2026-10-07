"""App-owned relational entities. Asset identity is distinct from a scoped topology node."""

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def now():
    return datetime.now(timezone.utc)


class Network(Base):
    __tablename__ = "app_topology_networks"
    id = Column(String(32), primary_key=True)
    owner_id = Column(String(64), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    domain = Column(String(64), nullable=False)
    current_snapshot = Column(String(32))
    active_run = Column(String(32))
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)
    # The owner's active topology: the one network the assistant's topology tools read.
    # At most one per owner (owner decision 2026-10-06).
    active = Column(Boolean, nullable=False, server_default=text("false"))
    __table_args__ = (
        Index(
            "uq_topology_active_network",
            "owner_id",
            unique=True,
            postgresql_where=text("active"),
            sqlite_where=text("active"),
        ),
    )


class DefaultLogin(Base):
    """One saved discovery username/password per platform account."""

    __tablename__ = "app_topology_default_logins"
    owner_id = Column(String(64), primary_key=True)
    username = Column(String(128), nullable=False)
    secret_ciphertext = Column(Text, nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=now)


class Run(Base):
    __tablename__ = "app_topology_runs"
    id = Column(String(32), primary_key=True)
    network_id = Column(String(32), ForeignKey(Network.id), nullable=False, index=True)
    owner_id = Column(String(64), nullable=False, index=True)
    request_id = Column(String(100), nullable=False)
    input_hash = Column(String(64), nullable=False)
    settings = Column(JSON, nullable=False)
    status = Column(String(32), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)
    finished_at = Column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("owner_id", "request_id"),)


class Target(Base):
    __tablename__ = "app_topology_targets"
    id = Column(String(32), primary_key=True)
    run_id = Column(String(32), ForeignKey(Run.id), nullable=False, index=True)
    target_key = Column(String(256), nullable=False)
    address = Column(String(64), nullable=False)
    depth = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False, index=True)
    reason = Column(String(128))
    credential_ref = Column(String(64))
    version = Column(Integer, nullable=False, default=1)
    lease_token = Column(String(32))
    lease_until = Column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("run_id", "target_key"),)


class Observation(Base):
    __tablename__ = "app_topology_observations"
    id = Column(String(32), primary_key=True)
    run_id = Column(String(32), ForeignKey(Run.id), nullable=False, index=True)
    target_id = Column(String(32), ForeignKey(Target.id), nullable=False)
    attempt = Column(Integer, nullable=False)
    observed_at = Column(DateTime(timezone=True), nullable=False)
    received_at = Column(DateTime(timezone=True), nullable=False, default=now)
    data = Column(JSON, nullable=False)
    evidence_key = Column(String(512), nullable=False)
    __table_args__ = (UniqueConstraint("target_id", "attempt"),)


class Node(Base):
    __tablename__ = "app_topology_nodes"
    network_id = Column(String(32), ForeignKey(Network.id), primary_key=True)
    node_id = Column(String(128), primary_key=True)
    asset_ref = Column(String(128))
    properties = Column(JSON, nullable=False)


class Alias(Base):
    __tablename__ = "app_topology_aliases"
    id = Column(String(64), primary_key=True)
    network_id = Column(String(32), nullable=False)
    node_id = Column(String(128), nullable=False)
    kind = Column(String(64), nullable=False)
    value = Column(String(256), nullable=False)
    status = Column(String(32), nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(
            ["network_id", "node_id"],
            ["app_topology_nodes.network_id", "app_topology_nodes.node_id"],
        ),
    )


class TerminationPoint(Base):
    __tablename__ = "app_topology_tps"
    network_id = Column(String(32), primary_key=True)
    node_id = Column(String(128), primary_key=True)
    tp_id = Column(String(128), primary_key=True)
    properties = Column(JSON, nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(
            ["network_id", "node_id"],
            ["app_topology_nodes.network_id", "app_topology_nodes.node_id"],
        ),
    )


class Link(Base):
    __tablename__ = "app_topology_links"
    network_id = Column(String(32), primary_key=True)
    link_id = Column(String(128), primary_key=True)
    source_node = Column(String(128), nullable=False)
    source_tp = Column(String(128), nullable=False)
    dest_node = Column(String(128), nullable=False)
    dest_tp = Column(String(128), nullable=False)
    properties = Column(JSON, nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(
            ["network_id", "source_node", "source_tp"],
            ["app_topology_tps.network_id", "app_topology_tps.node_id", "app_topology_tps.tp_id"],
        ),
        ForeignKeyConstraint(
            ["network_id", "dest_node", "dest_tp"],
            ["app_topology_tps.network_id", "app_topology_tps.node_id", "app_topology_tps.tp_id"],
        ),
    )


class Snapshot(Base):
    __tablename__ = "app_topology_snapshots"
    id = Column(String(32), primary_key=True)
    network_id = Column(String(32), ForeignKey(Network.id), nullable=False, index=True)
    run_id = Column(String(32), ForeignKey(Run.id), nullable=False, unique=True)
    graph = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)


class Resolution(Base):
    __tablename__ = "app_topology_resolutions"
    target_id = Column(String(32), ForeignKey(Target.id), primary_key=True)
    request_id = Column(String(100), primary_key=True)
    input_hash = Column(String(64), nullable=False)


class CollectionIssue(Base):
    """What a node could not give over SSH (the CLI) or NETCONF, for a human to fix.

    One row per device, capability, transport and kind; each observation updates it,
    and a later clean collection of the capability over that transport clears it.
    """

    __tablename__ = "app_topology_collection_issues"
    id = Column(String(32), primary_key=True)
    owner_id = Column(String(64), nullable=False, index=True)
    network_id = Column(String(32), nullable=False, index=True)
    address = Column(String(64), nullable=False)
    device = Column(String(255))  # the hostname the device gave, if any
    capability = Column(String(64), nullable=False)
    transport = Column(String(16), nullable=False)  # ssh, netconf
    kind = Column(String(128), nullable=False)
    detail = Column(JSON, nullable=False, default=dict)  # the latest occurrence
    count = Column(Integer, nullable=False, default=1)
    first_seen = Column(DateTime(timezone=True), nullable=False, default=now)
    last_seen = Column(DateTime(timezone=True), nullable=False, default=now)
    last_run_id = Column(String(32), nullable=False)
    last_observation_id = Column(String(32))
    cleared_at = Column(DateTime(timezone=True))  # None: still open
    __table_args__ = (
        UniqueConstraint("owner_id", "network_id", "address", "capability", "transport", "kind"),
    )
