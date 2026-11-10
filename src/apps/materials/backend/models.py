from sqlalchemy import JSON, BigInteger, Column, DateTime, Integer, String, Text
from sqlalchemy.orm import declarative_base, deferred

Base = declarative_base()


class Document(Base):
    __tablename__ = "app_materials_documents"
    id = Column(String(32), primary_key=True)
    title = Column(String(256), nullable=False)
    current_revision = Column(String(64), nullable=False)
    created_by = Column(String(64), nullable=False)
    status = Column(String(16), nullable=False, default="active", server_default="active")
    source_key = Column(String(64), unique=True)
    # "document" (Word/text, sections in the revision) or "docset" (CHM, pages in their own table).
    kind = Column(String(16), nullable=False, default="document", server_default="document")


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


class PageText(Base):
    """Content-addressed text; a refreshed manual shares every unchanged page with the last one."""

    __tablename__ = "app_materials_texts"
    sha256 = Column(String(64), primary_key=True)
    text = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)


class DocsetFile(Base):
    """One file of a doc-set revision's layout: a page, an alias of a page, TOC, index or README."""

    __tablename__ = "app_materials_docset_files"
    document_id = Column(String(32), primary_key=True)
    revision = Column(String(64), primary_key=True)
    path = Column(String(512), primary_key=True)
    role = Column(String(16), nullable=False)
    text_sha256 = Column(String(64), nullable=False)
    size = Column(Integer, nullable=False)
    lines = Column(Integer, nullable=False)
    title = Column(String(512))
    title_path = Column(JSON)
    toc_order = Column(Integer)
    doc_type = Column(String(32))
    features = Column(JSON)
    source_path = Column(String(512))
    canonical_path = Column(String(512))
    figures = Column(JSON)


class DocsetFigure(Base):
    __tablename__ = "app_materials_docset_figures"
    document_id = Column(String(32), primary_key=True)
    revision = Column(String(64), primary_key=True)
    sha256 = Column(String(64), primary_key=True)
    ext = Column(String(8), nullable=False)
    size = Column(Integer, nullable=False)


class FigureReading(Base):
    """A vision model's transcription of one figure, by image content: read once, reused."""

    __tablename__ = "app_materials_figure_readings"
    sha256 = Column(String(64), primary_key=True)
    model = Column(String(64), primary_key=True)
    prompt_version = Column(String(16), primary_key=True)
    text = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)


class ImportJob(Base):
    __tablename__ = "app_materials_import_jobs"
    id = Column(String(32), primary_key=True)
    actor_id = Column(String(64), nullable=False)
    filename = Column(String(255), nullable=False)
    size = Column(BigInteger, nullable=False)
    upload_key = Column(String(255), nullable=False)
    status = Column(String(16), nullable=False)
    attempts = Column(Integer, nullable=False, default=0)
    lease_token = Column(String(32))
    lease_until = Column(DateTime(timezone=True))
    error = Column(String(2000))
    document_id = Column(String(32))
    expected_revision = Column(String(64))
    source_sha256 = Column(String(64))
    normalized_sha256 = Column(String(64))
    summary_json = Column(JSON)
    manifest_json = deferred(Column(JSON))
    publish_json = Column(JSON)
    revision = Column(String(64))
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
