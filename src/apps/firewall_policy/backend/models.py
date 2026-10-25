from datetime import datetime, timezone

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def now():
    return datetime.now(timezone.utc)


class Upload(Base):
    __tablename__ = "app_firewall_uploads"
    id = Column(String(32), primary_key=True)
    owner_id = Column(String(64), nullable=False, index=True)
    request_id = Column(String(100), nullable=False)
    kind = Column(String(20), nullable=False)
    filename = Column(String(255), nullable=False)
    sha256 = Column(String(64), nullable=False)
    size = Column(Integer, nullable=False)
    object_key = Column(String(500), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)
    __table_args__ = (
        UniqueConstraint("owner_id", "request_id", name="uq_firewall_upload_request"),
    )


class Run(Base):
    __tablename__ = "app_firewall_runs"
    id = Column(String(32), primary_key=True)
    owner_id = Column(String(64), nullable=False, index=True)
    request_id = Column(String(100), nullable=False)
    kind = Column(String(20), nullable=False)
    parent_id = Column(String(32), ForeignKey("app_firewall_runs.id"), index=True)
    input_hash = Column(String(64), nullable=False)
    input_json = Column(JSON, nullable=False)
    status = Column(String(20), nullable=False, index=True)
    error = Column(String(2000))
    result_json = Column(JSON)
    artifacts_json = Column(JSON)
    attempts = Column(Integer, nullable=False, default=0)
    lease_token = Column(String(32))
    lease_until = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=now)
    __table_args__ = (UniqueConstraint("owner_id", "request_id", name="uq_firewall_run_request"),)
