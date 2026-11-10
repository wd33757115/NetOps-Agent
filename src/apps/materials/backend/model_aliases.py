"""Model names a doc set could add to its 适用型号与别名: suggested, never written by itself.

Two sources. The doc set's own 版本配套说明 page has a table mapping each device name to
the device shown in `display version` (CE6866-48S8CQ-P -> CE6800). The device inventory
has models of the same vendor that no doc set names yet. A person picks what to add.
"""

import re

from sqlalchemy import select

from .models import DocsetFile, PageText
from .repository import sessions

# Vendor names as inventories and manuals write them (Huawei / 华为), one key per vendor.
VENDORS = {
    "华为": "huawei", "huawei": "huawei", "新华三": "h3c", "华三": "h3c", "h3c": "h3c",
    "思科": "cisco", "cisco": "cisco", "锐捷": "ruijie", "ruijie": "ruijie",
    "中兴": "zte", "zte": "zte", "瞻博": "juniper", "juniper": "juniper",
}
MAPPING_TITLE = "版本配套"
MAPPING_COLUMNS = ("设备名称", "发布版本中的设备信息")
MODEL_NAME = re.compile(r"[A-Za-z]{1,8}\d[0-9A-Za-z]*(?:-[0-9A-Za-z]+)*")


def plain(text):
    return re.sub(r"\s+", "", text or "").casefold()


def vendor_of(text):
    """One name per vendor: the alias key it contains, else the text itself."""
    value = plain(text)
    return next((name for key, name in VENDORS.items() if key in value), value)


def vendor_named(text):
    """The vendor a text mentions, or None."""
    value = plain(text)
    return next((name for key, name in VENDORS.items() if key in value), None)


def model_names(metadata):
    return [n.strip() for n in re.split(r"[,，、;；/]", metadata.get("models", "")) if n.strip()]


def mapping_models(text):
    """Model names in the device-name columns of a 版本配套说明 page's tables."""
    found, columns = [], None  # None: the next table row is a header
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            columns = None
            continue
        cells = [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", line)[1:-1]]
        if columns is None:
            columns = [i for i, cell in enumerate(cells) if cell in MAPPING_COLUMNS]
            continue
        if all(set(cell) <= set("-: ") for cell in cells):
            continue
        found += [cells[i] for i in columns if i < len(cells) and MODEL_NAME.fullmatch(cells[i])]
    return list(dict.fromkeys(found))


def mapping_texts(files):
    """Texts of the pages titled 版本配套… among a revision's or an import's files."""
    shas = [f["text_sha256"] for f in files
            if f["role"] == "page" and MAPPING_TITLE in (f.get("title") or "")]
    if not shas:
        return []
    with sessions() as session:
        return list(session.scalars(select(PageText.text).where(PageText.sha256.in_(shas))))


def revision_files(document_id, revision):
    with sessions() as session:
        rows = session.execute(
            select(DocsetFile.role, DocsetFile.title, DocsetFile.text_sha256).where(
                DocsetFile.document_id == document_id,
                DocsetFile.revision == revision,
                DocsetFile.title.contains(MAPPING_TITLE),
            )
        ).all()
    return [{"role": r.role, "title": r.title, "text_sha256": r.text_sha256} for r in rows]


def inventory_models(vendor):
    """(model, device count) of inventory devices of one vendor."""
    from src.infrastructure.db.models import NetworkDevice
    from src.infrastructure.db.postgres import get_db_session

    counts = {}
    with get_db_session() as db:
        for device_vendor, model in db.query(NetworkDevice.vendor, NetworkDevice.model):
            if model and model.strip() and vendor_of(device_vendor) == vendor:
                counts[model.strip()] = counts.get(model.strip(), 0) + 1
    return sorted(counts.items())


def suggestions(files, vendor, named_elsewhere=()):
    """[{name, source}]: the mapping page's models, then inventory models no doc set names."""
    result = [{"name": name, "source": "版本配套说明"}
              for text in mapping_texts(files) for name in mapping_models(text)]
    seen = {plain(item["name"]) for item in result}
    key = vendor_of(vendor) if vendor else ""
    if key:
        covered = seen | {plain(name) for name in named_elsewhere}
        for model, count in inventory_models(key):
            if plain(model) not in covered:
                result.append({"name": model, "source": f"设备台账（{count} 台）"})
                covered.add(plain(model))
    return list({plain(item["name"]): item for item in result}.values())


def for_docset(document_id):
    """Suggestions for a published doc set's current revision."""
    from . import docsets

    current = next((d for d in docsets.catalog() if d["id"] == document_id), None)
    if current is None:
        from fastapi import HTTPException

        raise HTTPException(404, "文档集不存在或已停用")
    named = [name for d in docsets.catalog() for name in model_names(d["metadata"])]
    files = revision_files(document_id, current["revision"])
    return {"items": suggestions(files, current["metadata"].get("vendor", ""), named)}


def for_import(job_id, vendor):
    """Suggestions for a parsed import, with the vendor typed in its publish form."""
    from . import docsets

    with sessions() as session:
        files = docsets._manifest(session, job_id)["files"]
    named = [name for d in docsets.catalog() for name in model_names(d["metadata"])]
    return {"items": suggestions(files, vendor, named)}
