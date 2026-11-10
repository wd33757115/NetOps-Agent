"""Which product doc sets a question reads.

Rules choose, in order: the user's picks, doc sets this conversation already cited,
models and versions named in the question, then devices the turn's topology answers name.
The model can open more with open_docset. No more than LIMIT per question either way.
"""

import re

from src.apps.materials.backend.model_aliases import vendor_named, vendor_of

LIMIT = 3
REASONS = {
    "picked": "提问时选择",
    "earlier": "本会话之前引用过",
    "question": "问题中提到型号或版本",
    "device": "本轮路径上的设备型号",
    "model": "回答时按需展开",
}


def _plain(text):
    return re.sub(r"\s+", "", text or "").casefold()


def names(docset):
    meta = docset["metadata"]
    return [n.strip() for n in re.split(r"[,，、;；/]", meta.get("models", "")) if n.strip()] + [
        meta.get("product", "")
    ]


def mentioned(text, docset):
    """A model or product name, its model number, or the version.

    A series number covers its models: 6800 matches CE6881 and 6863 (68xx).
    """
    plain = _plain(text)
    if not plain:
        return False
    # A series number names another vendor's models too (H3C S6850 is not a CE 68xx).
    named, own = vendor_named(text), vendor_of(docset["metadata"].get("vendor", ""))
    series_apply = named is None or not own or named == own
    for name in names(docset):
        key = _plain(name)
        if len(key) >= 4 and key in plain:
            return True
        if not series_apply:
            continue
        for number in re.findall(r"\d{4,5}", name):
            series = len(number) == 4 and number.endswith("00")
            pattern = number[:2] + r"\d{2}" if series else number
            if re.search(rf"(?<!\d){pattern}(?!\d)", plain):
                return True
    version = _plain(docset["metadata"].get("software_version", ""))
    return len(version) >= 8 and version[:8] in plain


def for_device(device, docset):
    """An inventory record (vendor, model, os_version) the doc set covers."""
    meta = docset["metadata"]
    vendor, known = vendor_of(device.get("vendor")), vendor_of(meta.get("vendor"))
    if vendor and known and vendor != known:
        return False
    return mentioned(" ".join(filter(None, [device.get("model"), device.get("family")])), docset)


def version_rank(docset, text):
    """Prefer the version the question or device names, then the newest revision."""
    version = _plain(docset["metadata"].get("software_version", ""))
    return (bool(version) and version in _plain(text), docset["created_at"])


def choose(catalog, picked=(), earlier=(), question=""):
    """[(docset, reason)] in priority order, one version per product, at most LIMIT."""
    by_id = {d["id"]: d for d in catalog}
    chosen = [(by_id[i], "picked") for i in picked if i in by_id]
    chosen += [(by_id[i], "earlier") for i in earlier if i in by_id]
    matches = [d for d in catalog if mentioned(question, d)]
    # Two versions of one product: keep the one the question names, else the newest.
    best = {}
    for docset in sorted(matches, key=lambda d: version_rank(d, question), reverse=True):
        meta = docset["metadata"]
        best.setdefault((_plain(meta.get("vendor")), _plain(meta.get("product"))), docset)
    chosen += [(d, "question") for d in best.values()]
    result, seen = [], set()
    for docset, reason in chosen:
        if docset["id"] not in seen:
            seen.add(docset["id"])
            result.append((docset, reason))
    return result[:LIMIT]


def inventory(addresses):
    """Vendor, model and software version of devices by management address."""
    if not addresses:
        return []
    from src.infrastructure.db.models import NetworkDevice
    from src.infrastructure.db.postgres import get_db_session

    with get_db_session() as db:
        query = db.query(NetworkDevice).filter(NetworkDevice.management_ip.in_(list(addresses)))
        rows = query.all()
        return [
            {"address": r.management_ip, "vendor": r.vendor, "model": r.model,
             "family": r.family, "os_version": r.os_version}
            for r in rows
        ]
