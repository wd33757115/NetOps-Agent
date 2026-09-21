"""Document-level controls; immutable revisions continue to serve historical citations."""

from collections import defaultdict
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from . import library
from .documents import Section, digest
from .models import Document, Revision


class ExpectedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_status: Literal["active", "inactive"]


class StatusChange(ExpectedDocument):
    status: Literal["active", "inactive"]


class MetadataChange(library.ImportDocument):
    text: str = Field(default="", max_length=0, exclude=True)
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


def change_status(document_id, expected, status):
    with library.sessions() as session:
        document = session.scalar(
            select(Document).where(Document.id == document_id).with_for_update()
        )
        if document is None or document.status == "deleted":
            raise HTTPException(404, "资料不存在或已删除")
        if (
            document.current_revision != expected.expected_revision
            or document.status != expected.expected_status
        ):
            raise HTTPException(409, "资料修订或状态已变化，请刷新后重试")
        document.status = status
        if status == "deleted":
            document.source_key = None
        return {"id": document.id, "status": status, "revision": document.current_revision}


def update_metadata(actor_id, document_id, body):
    with library.sessions() as session:
        document = session.get(Document, document_id)
        if document is None or document.status == "deleted":
            raise HTTPException(404, "资料不存在或已删除")
        row = session.get(Revision, (document_id, document.current_revision))
        payload = library.ImportDocument(
            title=body.title,
            text=row.text,
            metadata=body.metadata,
            expected_revision=body.expected_revision,
        )
        sections = [
            Section(s["id"], tuple(s["title_path"]), s["start_line"], s["text"])
            for s in row.sections_json
        ]
        source = row.source_json
    return library.import_document(
        actor_id, payload, document_id, source=source, selected_sections=sections
    )


def compare(document_id, before, after, offset=0, limit=50):
    with library.sessions() as session:
        old = session.get(Revision, (document_id, before))
        new = session.get(Revision, (document_id, after))
        if old is None or new is None:
            raise HTTPException(404, "待比较的修订不存在")

        def indexed(sections):
            occurrences = defaultdict(int)
            result = {}
            for section in sections:
                path = tuple(section["title_path"])
                occurrences[path] += 1
                result[(path, occurrences[path])] = section
            return result

        older, newer = indexed(old.sections_json), indexed(new.sections_json)
        changes = []
        for key in dict.fromkeys([*older, *newer]):
            a, b = older.get(key), newer.get(key)
            if a and b and digest(a["text"]) == digest(b["text"]):
                continue
            changes.append(
                {
                    "kind": "added" if a is None else "removed" if b is None else "changed",
                    "title_path": list(key[0]),
                    "before_section": a["id"] if a else None,
                    "after_section": b["id"] if b else None,
                }
            )
        return {
            "before": before,
            "after": after,
            "title": {"before": old.title, "after": new.title},
            "metadata": {"before": old.metadata_json, "after": new.metadata_json},
            "counts": {
                kind: sum(c["kind"] == kind for c in changes)
                for kind in ("added", "removed", "changed")
            },
            "items": changes[offset : offset + limit],
            "next_offset": offset + limit if len(changes) > offset + limit else None,
        }
