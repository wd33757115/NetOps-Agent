"""One registry for document types, generation guidance, browser and Word styles."""
import json
from copy import deepcopy
from pathlib import Path
from src.apps.official_documents.backend.writing.docx_format import BODY_FONT, BODY_FONT_SIZE, BODY_LINE_SPACING

REGISTRY_PATH = Path(__file__).resolve().parents[1] / "skills/official-document-writing/assets/workspace-templates.json"
FORMAT = {
    "page_width_mm": 210, "page_height_mm": 297,
    "margin_top_mm": 37, "margin_right_mm": 26, "margin_bottom_mm": 35, "margin_left_mm": 28,
    "body_font": BODY_FONT, "body_size_pt": BODY_FONT_SIZE.pt, "line_height_pt": BODY_LINE_SPACING.pt,
    "title_font": "方正小标宋简体", "title_size_pt": 22,
    "heading1_font": "黑体", "heading2_font": "楷体_GB2312", "indent_chars": 2,
}


def catalog():
    data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    names = [t["doc_type"] for t in data["templates"]]
    if len(names) != len(set(names)):
        raise ValueError("文种模板名称重复")
    return {"version": data["version"], "templates": [{**t, "format": {**FORMAT, **t.get("format", {})}} for t in data["templates"]]}


def resolve_template(doc_type):
    templates = catalog()["templates"]
    value = next((t for t in templates if doc_type == t["doc_type"] or doc_type in t.get("aliases", [])), None)
    # Keep legacy/custom documents editable even when their registry entry was removed.
    return deepcopy(value or {"id": "custom", "doc_type": doc_type, "name": doc_type,
                              "sections": [], "closing": "", "recipient_required": False,
                              "headings_required": False, "format": FORMAT})
