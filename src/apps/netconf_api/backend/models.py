from datetime import datetime, timezone

from sqlalchemy import JSON, Column, DateTime, Integer, String
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def now():
    return datetime.now(timezone.utc)


class Definition(Base):
    """One NETCONF definition: the draft being edited and the version in use."""

    __tablename__ = "app_netconf_definitions"
    id = Column(String(120), primary_key=True)
    draft = Column(JSON)  # the stored form (mapping.load), or None
    draft_by = Column(String(64))
    draft_at = Column(DateTime(timezone=True))
    published_version = Column(Integer)  # None until first published
    revision = Column(Integer, nullable=False, default=1)  # bumped on every change


class DefinitionVersion(Base):
    """A published version; never changed afterwards."""

    __tablename__ = "app_netconf_definition_versions"
    definition_id = Column(String(120), primary_key=True)
    version = Column(Integer, primary_key=True)
    body = Column(JSON, nullable=False)
    sha256 = Column(String(64), nullable=False)
    check_item = Column(String(120))
    published_by = Column(String(64), nullable=False)
    published_at = Column(DateTime(timezone=True), nullable=False, default=now)
    note = Column(String(500))


class Capability(Base):
    """What a device's NETCONF hello announced, the last time it was read."""

    __tablename__ = "app_netconf_capabilities"
    device_id = Column(String(64), primary_key=True)
    status = Column(String(20), nullable=False)  # available, unavailable
    failure_code = Column(String(64))
    capabilities = Column(JSON, nullable=False, default=list)
    checked_by = Column(String(64), nullable=False)
    checked_at = Column(DateTime(timezone=True), nullable=False, default=now)


class Trial(Base):
    """One definition run against one device: the rows and problems it gave."""

    __tablename__ = "app_netconf_trials"
    id = Column(String(32), primary_key=True)
    owner_id = Column(String(64), nullable=False, index=True)
    device_id = Column(String(64), nullable=False)
    definition_id = Column(String(120), nullable=False)
    version = Column(Integer)  # None: the draft
    body_sha256 = Column(String(64), nullable=False)
    status = Column(String(20), nullable=False)  # succeeded, failed
    failure_code = Column(String(64))
    message = Column(String(500))
    read_count = Column(Integer)  # rows the device returned
    row_count = Column(Integer)  # rows output (``rows`` keeps the first TRIAL_ROWS)
    problem_count = Column(Integer)
    reply_bytes = Column(Integer)  # bytes of the device's replies (lookups included)
    rows = Column(JSON, nullable=False, default=list)
    problems = Column(JSON, nullable=False, default=list)
    filters = Column(JSON, nullable=False, default=list)  # [{definition, filter}]
    started_at = Column(DateTime(timezone=True), nullable=False, default=now)
    finished_at = Column(DateTime(timezone=True))
