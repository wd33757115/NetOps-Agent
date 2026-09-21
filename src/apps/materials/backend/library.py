"""Versioned shared library. Old citations always read their original snapshot."""

import json
from dataclasses import asdict
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from yaml import YAMLError

from .documents import FILTERS, MAX_BYTES, DocumentReader, Section, digest
from .models import Document, Revision
from .repository import now, sessions


class ImportDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=256)
    text: str = Field(min_length=1, max_length=MAX_BYTES)
    metadata: dict[str, str] = Field(default_factory=dict)
    expected_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("metadata")
    @classmethod
    def valid_metadata(cls, values):
        if set(values) - FILTERS or any(len(v) > 256 for v in values.values()):
            raise ValueError("Unsupported metadata")
        return values


def check_duplicate(checksum, document_id=None):
    with sessions() as session:
        other = session.scalar(
            select(Document)
            .join(
                Revision,
                (Document.id == Revision.document_id)
                & (Document.current_revision == Revision.revision),
            )
            .where(
                Revision.source_sha256 == checksum,
                Document.status != "deleted",
                Document.id != (document_id or ""),
            )
        )
        if other:
            raise HTTPException(
                409,
                f"文件已存在：{other.title}"
                f"（{'已停用' if other.status == 'inactive' else '已发布'}）。"
                "请在现有资料上更新修订或恢复使用。",
            )


def import_document(actor_id, payload, document_id=None, *, source=None, selected_sections=None):
    try:
        return _import_document(
            actor_id, payload, document_id, source=source, selected_sections=selected_sections
        )
    except IntegrityError as exc:
        raise HTTPException(409, "相同文件已被导入，请刷新资料库") from exc


def _import_document(actor_id, payload, document_id=None, *, source=None, selected_sections=None):
    raw = payload.text.encode("utf-8")
    if len(raw) > MAX_BYTES:
        raise HTTPException(413, "正文超过2 MiB，文件未发布")
    checksum = source["sha256"] if source else digest(raw)
    check_duplicate(checksum, document_id)
    try:
        _, embedded, sections = DocumentReader.parse(raw, payload.title)
    except (ValueError, UnicodeError, YAMLError) as exc:
        raise HTTPException(422, "资料格式无效") from exc
    if not sections:
        raise HTTPException(422, "资料没有可读取的正文")
    if selected_sections is not None:
        sections = selected_sections
    metadata = {**embedded, **payload.metadata}
    revision = digest(
        json.dumps(
            {
                "title": payload.title,
                "text": payload.text,
                "metadata": metadata,
                **({"source": source, "sections": [asdict(s) for s in sections]} if source else {}),
            },
            sort_keys=True,
            ensure_ascii=False,
        )
    )
    with sessions() as session:
        document = None
        if document_id:
            document = session.scalar(
                select(Document).where(Document.id == document_id).with_for_update()
            )
            if document is None or document.status == "deleted":
                raise HTTPException(404, "资料不存在")
            if payload.expected_revision != document.current_revision:
                raise HTTPException(409, "资料已变化，请刷新后再更新")
        elif payload.expected_revision:
            raise HTTPException(422, "新资料不应指定旧修订")
        else:
            document_id = uuid4().hex
            document = Document(
                id=document_id, title=payload.title, current_revision=revision, created_by=actor_id
            )
            session.add(document)
        if session.get(Revision, (document_id, revision)) is None:
            session.add(
                Revision(
                    document_id=document_id,
                    revision=revision,
                    title=payload.title,
                    source_sha256=source["sha256"] if source else digest(raw),
                    source_json=source,
                    metadata_json=metadata,
                    sections_json=[asdict(s) for s in sections],
                    text=payload.text,
                    created_at=now(),
                )
            )
        document.current_revision = revision
        document.title = payload.title
        document.source_key = checksum
        return {
            "id": document_id,
            "title": document.title,
            "revision": revision,
            "sections": len(sections),
            "metadata": metadata,
            "status": document.status or "active",
        }


def documents(*, include_inactive=False):
    with sessions() as session:
        return [
            {
                "id": d.id,
                "title": d.title,
                "revision": d.current_revision,
                "metadata": r.metadata_json,
                "created_at": r.created_at,
                "sections": len(r.sections_json),
                "has_original": bool(r.source_json),
                "status": d.status,
                "publication_scope": (r.source_json or {}).get(
                    "publication_scope", "legacy_selection" if r.source_json else "whole_file"
                ),
            }
            for d, r in session.execute(
                select(Document, Revision)
                .join(
                    Revision,
                    (
                        (Revision.document_id == Document.id)
                        & (Revision.revision == Document.current_revision)
                    ),
                )
                .where(
                    Document.status.in_(["active", "inactive"] if include_inactive else ["active"])
                )
                .order_by(Document.title)
            )
        ]


def revisions(document_id, offset=0, limit=20):
    with sessions() as session:
        if session.get(Document, document_id) is None:
            raise HTTPException(404, "资料不存在")
        rows = session.scalars(
            select(Revision)
            .where(Revision.document_id == document_id)
            .order_by(Revision.created_at.desc(), Revision.revision)
            .offset(offset)
            .limit(limit + 1)
        ).all()
        return {
            "items": [
                {
                    "revision": r.revision,
                    "title": r.title,
                    "created_at": r.created_at,
                    "sections": len(r.sections_json),
                    "metadata": r.metadata_json,
                    "has_original": bool(r.source_json),
                }
                for r in rows[:limit]
            ],
            "next_offset": offset + limit if len(rows) > limit else None,
        }


class LibraryReader(DocumentReader):
    def __init__(self):
        # No file path is accepted by this reader.
        pass

    def document_ids(self):
        with sessions() as session:
            return list(
                session.scalars(
                    select(Document.id).where(Document.status == "active").order_by(Document.id)
                )
            )

    def load(self, document_id, revision=None):
        with sessions() as session:
            document = session.get(Document, document_id)
            if document is None or (revision is None and document.status != "active"):
                raise ValueError("document_unavailable")
            row = session.get(Revision, (document_id, revision or document.current_revision))
            if row is None:
                raise ValueError("revision_unavailable")
            sections = [
                Section(s["id"], tuple(s["title_path"]), s["start_line"], s["text"])
                for s in row.sections_json
            ]
            metadata = dict(row.metadata_json)
            if row.source_json:
                metadata["extraction_status"] = "text_only_requires_review"
                metadata["extraction_warnings"] = row.source_json["warnings"]
            return row.revision, metadata, sections

    def read_section(self, document_id, revision, section_id, *, offset=0, limit=4000):
        result = super().read_section(document_id, revision, section_id, offset=offset, limit=limit)
        with sessions() as session:
            row = session.get(Revision, (document_id, revision))
            if row and row.source_json:
                source = row.source_json
                citation = result["citation"]
                locations = [
                    location
                    for location in source["locations"]
                    if location["start_line"] <= citation["end_line"]
                    and location["end_line"] >= citation["start_line"]
                ]
                citation["source"] = {
                    "format": "docx",
                    "filename": source["filename"],
                    "sha256": source["sha256"],
                    "warnings": source["warnings"],
                    "locations": locations[:50],
                    "locations_truncated": len(locations) > 50,
                }
        return result
