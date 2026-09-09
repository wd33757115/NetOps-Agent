"""Durable immutable template versions and user-scoped report drafting requests."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Column, DateTime, LargeBinary, String, Text

from src.infrastructure.db.models import Base


class PatrolReportTemplate(Base):
    __tablename__ = "netops_patrol_report_templates"
    id = Column(String(64), primary_key=True)
    name = Column(String(128), nullable=False)
    content = Column(LargeBinary, nullable=False)
    digest = Column(String(64), nullable=False, unique=True)
    owner_id = Column(String(64), nullable=False)
    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )


class PatrolReportDraft(Base):
    __tablename__ = "netops_patrol_report_drafts"
    id = Column(String(64), primary_key=True, default=lambda: uuid.uuid4().hex)
    job_id = Column(String(64), nullable=False, index=True)
    owner_id = Column(String(64), nullable=False, index=True)
    template_id = Column(String(64), nullable=False)
    state = Column(String(24), nullable=False, default="preview")
    digest = Column(String(64), nullable=False)
    source_digest = Column(String(64), nullable=False)
    provider_digest = Column(String(64), nullable=False)
    packet = Column(JSON, nullable=False)
    output = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    accepted_at = Column(DateTime(timezone=True), nullable=True)
