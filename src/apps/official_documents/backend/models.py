from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, DateTime, JSON, ForeignKey, UniqueConstraint, Text
from src.infrastructure.db.models import Base


def now():
    return datetime.now(timezone.utc)


class OfficialDocument(Base):
    __tablename__ = "official_documents"
    id = Column(String(64), primary_key=True)
    owner_id = Column(String(64), nullable=False, index=True)
    title = Column(String(500), nullable=False)
    document_type = Column(String(30), nullable=False)
    status = Column(String(20), nullable=False, default="draft")
    revision = Column(Integer, nullable=False, default=0)
    current_version = Column(Integer, nullable=False, default=1)
    conversation_id = Column(String(64), nullable=False, unique=True)
    create_key = Column(String(100), nullable=False)
    create_hash = Column(String(64), nullable=False)
    mutation_receipts = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=now)
    deleted_at = Column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("owner_id", "create_key", name="uq_official_document_create"),)


class OfficialDocumentVersion(Base):
    __tablename__ = "official_document_versions"
    id = Column(String(64), primary_key=True)
    document_id = Column(String(64), ForeignKey("official_documents.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    content_json = Column(JSON, nullable=False)
    content_hash = Column(String(64), nullable=False)
    plain_text = Column(Text, nullable=False)
    change_source = Column(String(20), nullable=False)
    change_summary = Column(String(500), nullable=False)
    created_by = Column(String(64), nullable=False)
    request_id = Column(String(100), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)
    __table_args__ = (UniqueConstraint("document_id", "version", name="uq_official_document_version"), UniqueConstraint("document_id", "request_id", name="uq_official_document_save"))


class OfficialDocumentOperation(Base):
    __tablename__ = "official_document_operations"
    id = Column(String(64), primary_key=True)
    document_id = Column(String(64), ForeignKey("official_documents.id"), nullable=False, index=True)
    owner_id = Column(String(64), nullable=False)
    kind = Column(String(20), nullable=False)
    base_revision = Column(Integer, nullable=False)
    base_version = Column(Integer, nullable=False)
    request_key = Column(String(100), nullable=False)
    request_hash = Column(String(64), nullable=False)
    status = Column(String(20), nullable=False, default="queued", index=True)
    input_json = Column(JSON, nullable=False)
    result_json = Column(JSON)
    error_code = Column(String(100))
    worker_token = Column(String(64))
    lease_expires_at = Column(DateTime(timezone=True))
    attempt = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=now)
    __table_args__ = (UniqueConstraint("document_id", "request_key", name="uq_official_document_operation"),)
