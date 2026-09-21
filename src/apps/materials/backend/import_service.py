"""Preview is stateless; publication re-extracts and publishes the complete file."""

from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

from defusedxml.common import DefusedXmlException
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import docx_import, library
from .documents import Section, digest
from .models import Revision
from .repository import sessions

MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PREFIX = "apps/materials/sources/"


class Publication(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=256)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    normalized_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    # Accept old clients only when their selection covers the entire document.
    section_ids: list[str] | None = Field(default=None, min_length=1, max_length=2000)
    metadata: dict[str, str] = Field(default_factory=dict)
    acknowledged_limitations: bool = False
    document_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    expected_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


def storage():
    from src.infrastructure.storage.minio_client import get_minio_storage

    return get_minio_storage()


def preview(data, filename):
    try:
        result = docx_import.extract(data, filename)
        return {k: v for k, v in result.items() if k not in {"text", "locations"}}
    except (ValueError, ParseError, DefusedXmlException, BadZipFile, RecursionError) as exc:
        raise HTTPException(422, "Word解析失败：文件结构、大小或正文不受支持") from exc


def publish(actor_id, data, filename, plan):
    if not plan.acknowledged_limitations:
        raise HTTPException(422, "请核对提取预览及缺失内容提示")
    # Validate metadata before object storage writes.
    payload = library.ImportDocument(
        title=plan.title,
        text="pending",
        metadata=plan.metadata,
        expected_revision=plan.expected_revision,
    )
    try:
        result = docx_import.extract(data, filename)
    except (ValueError, ParseError, DefusedXmlException, BadZipFile, RecursionError) as exc:
        raise HTTPException(422, "Word解析失败，请重新预览") from exc
    if (
        plan.source_sha256 != result["source_sha256"]
        or plan.normalized_sha256 != result["normalized_sha256"]
    ):
        raise HTTPException(409, "文件或解析结果已变化，请重新预览")
    selected = result["sections"]
    if plan.section_ids is not None and (
        len(set(plan.section_ids)) != len(plan.section_ids)
        or set(plan.section_ids) != {s["id"] for s in selected}
    ):
        raise HTTPException(422, "现在按整份文件发布，请刷新页面后重新发布")
    sections = [
        Section(s["id"], tuple(s["title_path"]), s["start_line"], s["text"]) for s in selected
    ]
    payload = library.ImportDocument(
        title=plan.title,
        text="".join(s.text for s in sections),
        metadata=plan.metadata,
        expected_revision=plan.expected_revision,
    )
    if len(payload.text.encode("utf8")) > docx_import.MAX_BYTES:
        raise HTTPException(413, "提取正文超过2 MiB，文件未发布")
    library.check_duplicate(result["source_sha256"], plan.document_id)
    key = PREFIX + result["source_sha256"] + ".docx"
    backend = storage()
    if not backend.upload_file(key, data, MIME) or backend.download_file(key) != data:
        raise HTTPException(503, "原文件保存或校验失败，资料未发布")
    source = {
        "key": key,
        "filename": filename,
        "sha256": result["source_sha256"],
        "normalized_sha256": result["normalized_sha256"],
        "warnings": result["warnings"],
        "section_ids": [s.id for s in sections],
        "publication_scope": "whole_file",
        "locations": result["locations"],
    }
    return library.import_document(
        actor_id, payload, plan.document_id, source=source, selected_sections=sections
    )


def original(document_id, revision):
    with sessions() as session:
        row = session.get(Revision, (document_id, revision))
        if row is None or not row.source_json:
            raise HTTPException(404, "本修订没有原始Word文件")
        source = dict(row.source_json)
    expected_key = PREFIX + source["sha256"] + ".docx"
    if source["key"] != expected_key:
        raise HTTPException(409, "原文件位置校验失败")
    data = storage().download_file(expected_key)
    if data is None or digest(data) != source["sha256"]:
        raise HTTPException(503, "原文件缺失或校验失败")
    return source["filename"], data
