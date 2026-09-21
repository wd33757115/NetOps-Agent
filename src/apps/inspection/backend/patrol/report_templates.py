"""Validated DOCX templates. Uploaded documents are never executed or sent to AI."""

import json
import re
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from lxml import etree

ASSETS = Path(__file__).with_name("report_assets")
DEFAULT_TEMPLATE = "builtin-v1"
NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
MAX_UPLOAD = 8 * 1024 * 1024


def builtin_bytes():
    return (ASSETS / "patrol-v1.docx").read_bytes()


def binding_schema():
    return json.loads((ASSETS / "bindings.json").read_text(encoding="utf-8"))


def controls(content):
    with ZipFile(BytesIO(content)) as z:
        root = etree.fromstring(
            z.read("word/document.xml"), etree.XMLParser(resolve_entities=False, no_network=True)
        )
        return root.xpath("//w:sdtPr/w:tag/@w:val", namespaces=NS)


def validate_template(content):
    if len(content) > MAX_UPLOAD:
        raise ValueError("模板不得超过 8 MB")
    try:
        with ZipFile(BytesIO(content)) as z:
            entries = z.infolist()
            if len(entries) > 500 or sum(e.file_size for e in entries) > 40 * 1024 * 1024:
                raise ValueError("模板解压后过大")
            if len(set(z.namelist())) != len(entries):
                raise ValueError("模板包含重复压缩条目")
            for e in entries:
                name = e.filename.lower()
                if (
                    ".." in name.split("/")
                    or name.startswith("/")
                    or any(
                        x in name for x in ["vbaproject", "embeddings/", "activex/", "externallink"]
                    )
                ):
                    raise ValueError("模板包含不支持的嵌入内容")
                if name.endswith((".xml", ".rels")):
                    raw = z.read(e)
                    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
                        raise ValueError("模板包含 XML 实体")
                    root = etree.fromstring(
                        raw, etree.XMLParser(resolve_entities=False, no_network=True)
                    )
                    if name.endswith(".rels") and root.xpath(
                        '//*[local-name()="Relationship" and @TargetMode="External"]'
                    ):
                        raise ValueError("模板不能包含外部链接")
                    if root.xpath(
                        '//*[local-name()="altChunk" or local-name()="object" '
                        'or local-name()="dataBinding"]'
                    ):
                        raise ValueError("模板不能包含嵌入对象或外部数据绑定")
                    instructions = " ".join(
                        root.xpath('//*[local-name()="instrText"]/text()')
                        + root.xpath('//*[local-name()="fldSimple"]/@*[local-name()="instr"]')
                    )
                    if re.search(
                        r"\b(DDE|INCLUDETEXT|INCLUDEPICTURE|LINK|DATABASE|HYPERLINK)\b",
                        instructions,
                        re.I,
                    ):
                        raise ValueError("模板包含不支持的动态字段")
        tags = controls(content)
        known = set(binding_schema()["tags"])
        if not tags or len(tags) != len(set(tags)) or set(tags) - known:
            raise ValueError("模板控件缺失、重复或未登记，请从内置模板下载后编辑")
        required = {"conclusion.text", "conclusion.basis", "narrative.limitations"}
        if not required <= set(tags):
            raise ValueError("模板必须保留结论、依据和覆盖限制控件")
        with ZipFile(BytesIO(content)) as z:
            root = etree.fromstring(
                z.read("word/document.xml"),
                etree.XMLParser(resolve_entities=False, no_network=True),
            )
        for row in root.xpath("//w:tr", namespaces=NS):
            prefixes = {
                tag.rsplit(".", 1)[0]
                for tag in row.xpath(".//w:sdtPr/w:tag/@w:val", namespaces=NS)
                if tag.rsplit(".", 1)[-1].isdigit()
            }
            if len(prefixes) > 1:
                raise ValueError("同一重复行不能混合不同数据表的控件")
        seen = set()
        body = root.find("w:body", NS)
        if body is None:
            raise ValueError("模板缺少正文")
        for element in body:
            element_tags = set(element.xpath(".//w:sdtPr/w:tag/@w:val", namespaces=NS))
            text = "".join(element.xpath(".//w:t/text()", namespaces=NS))
            if text.startswith("F 非结构化文本变化对照") or any(
                tag.startswith("cli.") for tag in element_tags
            ):
                if not required <= seen:
                    raise ValueError("结论和覆盖限制必须位于动态 CLI 附录之前")
                break
            seen.update(element_tags)
    except (BadZipFile, KeyError, etree.XMLSyntaxError) as exc:
        raise ValueError("不是有效的 DOCX 模板") from exc
    return sha256(content).hexdigest()


def template_catalog():
    from src.infrastructure.db.postgres import get_db_session

    from src.core.patrol.report_models import PatrolReportTemplate

    result = [
        {
            "id": DEFAULT_TEMPLATE,
            "name": "网络设备巡检报告（问题与变化）",
            "digest": sha256(builtin_bytes()).hexdigest(),
            "builtin": True,
        }
    ]
    with get_db_session() as db:
        result.extend(
            {"id": r.id, "name": r.name, "digest": r.digest, "builtin": False}
            for r in db.query(PatrolReportTemplate)
            .order_by(PatrolReportTemplate.created_at.desc())
            .all()
        )
    return result


def load_template(identifier=DEFAULT_TEMPLATE):
    if identifier == DEFAULT_TEMPLATE:
        return builtin_bytes()
    from src.infrastructure.db.postgres import get_db_session

    from src.core.patrol.report_models import PatrolReportTemplate

    with get_db_session() as db:
        row = db.get(PatrolReportTemplate, identifier)
        if row is None:
            raise ValueError("模板不存在")
        return bytes(row.content)


def import_template(content, name, owner):
    from src.infrastructure.db.postgres import get_db_session

    from src.core.patrol.report_models import PatrolReportTemplate

    digest = validate_template(content)
    with get_db_session() as db:
        existing = db.query(PatrolReportTemplate).filter_by(digest=digest).first()
        if existing:
            return {"id": existing.id, "name": existing.name, "digest": digest}
        row = PatrolReportTemplate(
            id=digest[:32],
            name=name.strip()[:128] or "巡检模板",
            content=content,
            digest=digest,
            owner_id=owner,
        )
        db.add(row)
    return {"id": digest[:32], "name": name, "digest": digest}
