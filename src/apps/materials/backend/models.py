from sqlalchemy import JSON, Column, DateTime, String, Text
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Document(Base):
    __tablename__ = "app_materials_documents"
    id = Column(String(32), primary_key=True)
    title = Column(String(256), nullable=False)
    current_revision = Column(String(64), nullable=False)
    created_by = Column(String(64), nullable=False)
    status = Column(String(16), nullable=False, default="active", server_default="active")
    source_key = Column(String(64), unique=True)


class Revision(Base):
    __tablename__ = "app_materials_revisions"
    document_id = Column(String(32), primary_key=True)
    revision = Column(String(64), primary_key=True)
    title = Column(String(256), nullable=False)
    source_sha256 = Column(String(64), nullable=False)
    metadata_json = Column(JSON, nullable=False)
    sections_json = Column(JSON, nullable=False)
    source_json = Column(JSON)
    text = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
