from datetime import datetime, timezone

from sqlalchemy import JSON, Column, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import declarative_base

Base = declarative_base()
# The platform keeps its management-domain parameter; this App always passes one value.
DOMAIN = "default"


def now():
    return datetime.now(timezone.utc)


class SavedSession(Base):
    __tablename__ = "app_device_access_saved_sessions"
    id = Column(String(32), primary_key=True)
    owner_id = Column(String(64), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    host = Column(String(45), nullable=False)
    port = Column(Integer, nullable=False, default=22)
    # Both empty: log in with the owner's default username/password.
    username = Column(String(128))
    secret_ciphertext = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=now)


class DefaultLogin(Base):
    __tablename__ = "app_device_access_default_logins"
    owner_id = Column(String(64), primary_key=True)
    username = Column(String(128), nullable=False)
    secret_ciphertext = Column(Text, nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=now)


class Operation(Base):
    __tablename__ = "app_device_access_operations"
    id = Column(String(32), primary_key=True)
    owner_id = Column(String(64), nullable=False, index=True)
    session_id = Column(String(32))
    host = Column(String(45), nullable=False)
    request_id = Column(String(100), nullable=False)
    profile_id = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False)
    result = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)
    finished_at = Column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("owner_id", "request_id"),)


class HostKey(Base):
    __tablename__ = "app_device_access_host_keys"
    domain = Column(String(64), primary_key=True)
    host = Column(String(45), primary_key=True)
    port = Column(Integer, primary_key=True)
    key_type = Column(String(64), nullable=False)
    public_key = Column(Text, nullable=False)
    fingerprint_sha256 = Column(String(64), nullable=False)
    first_seen = Column(DateTime(timezone=True), nullable=False, default=now)
    # pending: seen once, waiting for the user; trusted: confirmed or recorded by discovery.
    status = Column(String(16), nullable=False)
    revision = Column(Integer, nullable=False, default=1)
    verified_by = Column(String(64))
    verified_at = Column(DateTime(timezone=True))
    observed_fingerprint = Column(String(64))
    observed_key_type = Column(String(64))
    observed_public_key = Column(Text)
    changed_at = Column(DateTime(timezone=True))


class SessionAudit(Base):
    __tablename__ = "app_device_access_session_audit"
    id = Column(String(32), primary_key=True)
    owner_id = Column(String(64), nullable=False, index=True)
    session_id = Column(String(32), nullable=False, index=True)
    target_ref = Column(String(256), nullable=False)
    event_kind = Column(String(32), nullable=False)
    payload = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)
