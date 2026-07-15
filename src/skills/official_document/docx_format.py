# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""公文 DOCX 段落/页面格式（GB/T 9704-2012 常用值）。"""

from __future__ import annotations

from docx.enum.section import WD_ORIENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

BODY_FONT = "仿宋_GB2312"
BODY_FONT_SIZE = Pt(16)
# 固定行距 28 磅（必须用 Pt + EXACTLY，不能写裸数字 28，否则会被当成 28 倍行距）
BODY_LINE_SPACING = Pt(28)


def setup_a4_margins(doc) -> None:
    for section in doc.sections:
        section.orientation = WD_ORIENT.PORTRAIT
        section.page_width = Cm(21.0)
        section.page_height = Cm(29.7)
        section.top_margin = Cm(3.7)
        section.left_margin = Cm(2.8)
        section.right_margin = Cm(2.6)
        section.bottom_margin = Cm(3.5)


def apply_fixed_line_spacing(paragraph) -> None:
    pf = paragraph.paragraph_format
    pf.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    pf.line_spacing = BODY_LINE_SPACING
    pf.space_before = Pt(0)
    pf.space_after = Pt(0)


def style_run(run, *, bold: bool = False) -> None:
    run.font.name = BODY_FONT
    run.font.size = BODY_FONT_SIZE
    run.font.bold = bold
    run._element.rPr.rFonts.set(qn("w:eastAsia"), BODY_FONT)


def add_styled_paragraph(
    doc,
    text: str,
    *,
    center: bool = False,
    right: bool = False,
    first_line_indent: float | None = None,
    bold: bool = False,
):
    """添加一段公文正文段落。"""
    if not text or not str(text).strip():
        return None
    p = doc.add_paragraph()
    if center:
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    elif right:
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    if first_line_indent is not None:
        p.paragraph_format.first_line_indent = Cm(first_line_indent)
    apply_fixed_line_spacing(p)
    run = p.add_run(str(text).strip())
    style_run(run, bold=bold)
    return p
