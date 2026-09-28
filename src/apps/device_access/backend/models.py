from datetime import datetime, timezone

from sqlalchemy import JSON, Column, DateTime, ForeignKey, String, UniqueConstraint
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
