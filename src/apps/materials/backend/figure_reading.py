"""Figure reading: a vision model transcribes one doc-set figure into text, cached by image.

Network diagrams in configuration examples carry device names, links and addresses that
the page text leaves out ("interface1 代表 100GE1/0/1"). Questions call this on demand;
nothing is read at import. The reading is the model's, never the manual's own words, so
a citation keeps the figure beside it for the user to check.
"""

import base64
import io
import os

import httpx
from PIL import Image
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as upsert

from .docsets import _figure_bytes, sessions
from .documents import digest
from .models import DocsetFigure, DocsetFile, Document, FigureReading, Revision
from .repository import now

PROMPT_VERSION = "1"
# Asking where each label sits (what its arrow points at) instead of whose it is: asked to
# summarize roles, the model put both VBDIF gateways of a VXLAN example on the spine.
PROMPT = (
    "这是网络设备产品手册里的一张插图。请用中文转写图中能看清的内容，只写看得到的，不推断、不归纳角色：\n"
    "1. 设备与连线：逐条写「A 的哪个接口 — B 的哪个接口」，接口名按标在哪一端写在哪一端。\n"
    "2. 标注：图中每个文字标注或文本框单独一行，写出原文，并写明它的位置——有箭头或引线的写"
    "「箭头指向 X」，没有的写「位于 X 旁边」。\n"
    "3. 其他文字（图例、隧道、网段、VLAN、服务器地址）原样列出，并写明在哪台设备或哪个区域旁。\n"
    "看不清的写「看不清」；图上没有标注的不要补。只输出转写内容。"
)
LONG_SIDE = 1600
MAX_IMAGE_BYTES = 4 * 1024 * 1024
# Low-effort reasoning reads links right (15-26 s on two diagrams) where none misreads them;
# full reasoning ran 12k tokens without an answer. The second attempt fits the tool's 75 s.
ATTEMPTS = (
    ({"thinking": {"type": "enabled"}, "reasoning_effort": "low", "max_tokens": 8000}, 45),
    ({"thinking": {"type": "disabled"}, "max_tokens": 4000}, 20),
)


def model():
    return os.environ.get("MATERIALS_FIGURE_MODEL", "deepseek-v4-flash-vision-exp")


def prepared(data):
    """PNG no longer than LONG_SIDE on its longest side; the first frame of an animation."""
    image = Image.open(io.BytesIO(data))
    image.seek(0)
    image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    image.thumbnail((LONG_SIDE, LONG_SIDE))
    out = io.BytesIO()
    image.save(out, "PNG", optimize=True)
    png = out.getvalue()
    if len(png) > MAX_IMAGE_BYTES:
        raise ValueError("figure_too_large")
    return png


def transcribe(png):
    from src.common.config import get_settings

    settings = get_settings()
    endpoint = settings.DEEPSEEK_BASE_URL.rstrip("/")
    if not endpoint.startswith("https://") or not settings.DEEPSEEK_API_KEY:
        raise ValueError("figure_reading_unavailable")
    image = "data:image/png;base64," + base64.b64encode(png).decode()
    messages = [{"role": "user", "content": [
        {"type": "text", "text": PROMPT},
        {"type": "image_url", "image_url": {"url": image}},
    ]}]
    for options, seconds in ATTEMPTS:
        try:
            response = httpx.post(
                endpoint + "/chat/completions",
                headers={"Authorization": "Bearer " + settings.DEEPSEEK_API_KEY},
                json={"model": model(), "messages": messages, **options},
                timeout=seconds,
                trust_env=False,
            )
        except httpx.HTTPError:
            continue
        if response.status_code != 200:
            continue
        # Reasoning that used the whole budget leaves the answer empty.
        text = (response.json()["choices"][0]["message"].get("content") or "").strip()
        if text:
            return text
    raise ValueError("figure_reading_unavailable")


def reading(sha, ext):
    """The cached transcription of one image, made on first use; (text, model, cached)."""
    name = model()
    with sessions() as session:
        cached = session.get(FigureReading, (sha, name, PROMPT_VERSION))
        if cached is not None:
            return cached.text, name, True
    data, _ = _figure_bytes(sha, ext)
    text = transcribe(prepared(data))
    with sessions() as session:
        session.execute(
            upsert(FigureReading)
            .values(sha256=sha, model=name, prompt_version=PROMPT_VERSION, text=text,
                    created_at=now())
            .on_conflict_do_nothing()
        )
    return text, name, False


def _page_figure(session, document_id, revision, path, sha, ext):
    """The page row, when the figure is one of that page's figures in that revision."""
    row = session.get(DocsetFile, (document_id, revision, path))
    if row is None or row.role != "page" or sha not in (row.figures or []):
        return None
    stored = session.scalar(
        select(DocsetFigure.ext).where(
            DocsetFigure.document_id == document_id,
            DocsetFigure.revision == revision,
            DocsetFigure.sha256 == sha,
        )
    )
    return row if stored == ext else None


def citation(document_id, revision, path, sha, ext):
    """A figure of one page with its reading, kept with the answer like an excerpt."""
    with sessions() as session:
        row = _page_figure(session, document_id, revision, path, sha, ext)
        if row is None:
            raise ValueError("citation_unavailable")
        title_path, title, original = list(row.title_path or []), row.title, row.source_path
        filename = (session.get(Revision, (document_id, revision)).source_json or {}).get(
            "filename"
        )
        docset = session.get(Document, document_id).title
    text, name, _ = reading(sha, ext)
    return {
        "kind": "docset_figure",
        "id": "fig-" + digest(f"{document_id}|{revision}|{path}|{sha}")[:20],
        "document_id": document_id,
        "revision": revision,
        "path": path,
        "figure": f"{sha}.{ext}",
        "title_path": title_path or [title],
        "reading": text,
        "reading_sha256": digest(text),
        "model": name,
        "source": {"format": "chm", "docset": docset, "filename": filename, "page": original},
    }


def validate_citation(reference):
    """The figure still belongs to that page's revision; the reading is the one answered with."""
    sha, _, ext = reference["figure"].partition(".")
    with sessions() as session:
        row = _page_figure(
            session, reference["document_id"], reference["revision"], reference["path"], sha, ext
        )
    if row is None or digest(reference["reading"]) != reference["reading_sha256"]:
        raise ValueError("citation_mismatch")


def image(reference):
    """The cited figure's bytes and media type, after the citation checks out."""
    validate_citation(reference)
    sha, _, ext = reference["figure"].partition(".")
    return _figure_bytes(sha, ext)
