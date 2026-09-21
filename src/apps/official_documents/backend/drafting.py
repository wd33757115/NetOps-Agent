"""Adapt the existing writing Skill's whole-document contract to editable blocks."""
import uuid
from typing import Literal
from pydantic import Field
from src.apps.official_documents.backend.writing.schema import OfficialDocumentJSON
from .schemas import StrictModel, Content


class DraftResult(StrictModel):
    kind: Literal["proposal", "clarification"]
    message: str
    questions: list[str] = Field(default_factory=list, max_length=10)
    document: OfficialDocumentJSON | None = None
    unresolved_facts: list[str] = Field(default_factory=list, max_length=30)


def to_workspace(document: OfficialDocumentJSON, previous: dict, unresolved_facts: list[str]):
    blocks = []
    old_ids = {}
    for block in previous["blocks"]:
        old_ids.setdefault(block["role"], []).append(block["id"])

    def append(role, text, level=1):
        text = text.strip()
        if not text:
            return
        available = old_ids.get(role, [])
        blocks.append({"id": available.pop(0) if available else uuid.uuid4().hex, "role": role, "level": level, "runs": [{"text": text, "marks": []}]})

    append("issuer", document.issuer)
    append("title", document.title)
    append("main_recipient", document.main_recipient.rstrip("：:") + "：" if document.main_recipient.strip() else "")
    append("opening", document.main_body.opening)
    for section in document.main_body.sections:
        append("section_heading", section.heading)
        for paragraph in section.content.splitlines():
            append("body", paragraph)
    append("closing", document.main_body.closing)
    append("signature_org", document.signature.org)
    append("signature_date", document.signature.date)
    from .templates import resolve_template
    template = resolve_template(document.doc_type.strip() or previous["doc_type"])
    content = Content(doc_type=template["doc_type"], blocks=blocks, unresolved_facts=unresolved_facts).model_dump()
    required = {"title", "opening", "body", "signature_org", "signature_date"}
    if template.get("headings_required", True):
        required.add("section_heading")
    if template["recipient_required"]:
        required.add("main_recipient")
    if not required.issubset({b["role"] for b in blocks}):
        raise ValueError("完整公文缺少结构要素，请补充事实后重试")
    return content
