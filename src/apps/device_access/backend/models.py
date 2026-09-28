from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def now():
    return datetime.now(timezone.utc)


class Connection(Base):
    __tablename__ = "app_device_access_connections"
    id = Column(String(32), primary_key=True)
    owner_id = Column(String(64), nullable=False, index=True)
    settings = Column(JSON, nullable=False)
    credential_ref = Column(String(64), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)


class Operation(Base):
    __tablename__ = "app_device_access_operations"
    id = Column(String(32), primary_key=True)
    owner_id = Column(String(64), nullable=False, index=True)
    connection_id = Column(String(32), ForeignKey(Connection.id), nullable=False)
    request_id = Column(String(100), nullable=False)
    profile_id = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False)
    result = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)
    finished_at = Column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("owner_id", "request_id"),)


class CredentialSet(Base):
    __tablename__ = "app_device_access_credential_sets"
    id = Column(String(32), primary_key=True)
    owner_id = Column(String(64), nullable=False, index=True)
    domain = Column(String(64), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    username = Column(String(128), nullable=False)
    secret_ciphertext = Column(Text, nullable=False)
    enable_secret_ciphertext = Column(Text)
    scope_kind = Column(String(16), nullable=False)
    scope_value = Column(String(256), nullable=False)
    priority = Column(Integer, nullable=False, default=0)
    disabled = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)
    rotated_at = Column(DateTime(timezone=True), nullable=False, default=now)


class DeviceCredential(Base):
    __tablename__ = "app_device_access_device_credentials"
    owner_id = Column(String(64), primary_key=True)
    domain = Column(String(64), primary_key=True)
    device_key = Column(String(256), primary_key=True)
    credential_set_id = Column(String(32), ForeignKey(CredentialSet.id))
    last_success_at = Column(DateTime(timezone=True))
    last_failure_code = Column(String(64))


class HostKey(Base):
    __tablename__ = "app_device_access_host_keys"
    domain = Column(String(64), primary_key=True)
    host = Column(String(45), primary_key=True)
    port = Column(Integer, primary_key=True)
    key_type = Column(String(64), nullable=False)
    public_key = Column(Text, nullable=False)
    fingerprint_sha256 = Column(String(64), nullable=False)
    first_seen = Column(DateTime(timezone=True), nullable=False, default=now)
    status = Column(String(16), nullable=False)
    revision = Column(Integer, nullable=False, default=1)
    verified_by = Column(String(64))
    verified_at = Column(DateTime(timezone=True))
    observed_fingerprint = Column(String(64))
    changed_at = Column(DateTime(timezone=True))


class HostKeyPolicy(Base):
    __tablename__ = "app_device_access_host_key_policies"
    domain = Column(String(64), primary_key=True)
    policy = Column(String(16), nullable=False)
    updated_by = Column(String(64), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=now)


class SessionAudit(Base):
    __tablename__ = "app_device_access_session_audit"
    id = Column(String(32), primary_key=True)
    owner_id = Column(String(64), nullable=False, index=True)
    session_id = Column(String(32), nullable=False, index=True)
    target_ref = Column(String(256), nullable=False)
    event_kind = Column(String(32), nullable=False)
    payload = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)
