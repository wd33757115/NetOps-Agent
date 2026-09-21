"""Local-only DOCX extraction. Never imports a model client or follows relationships."""

import io
import json
import re
import zlib
from dataclasses import asdict
from pathlib import PurePosixPath
from zipfile import BadZipFile, ZipFile

from defusedxml import ElementTree

from .documents import MAX_BYTES, DocumentReader, digest

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MAX_UPLOAD = 16 * 1024 * 1024


def text_of(element):
    pieces = []

    def visit(child):
        if child.tag in {W + name for name in ("del", "moveFrom", "drawing", "pict", "object")}:
            return
        if child.tag == W + "t":
            pieces.append(child.text or "")
        elif child.tag == W + "tab":
            pieces.append("\t")
        elif child.tag in {W + "br", W + "cr"}:
            pieces.append("\n")
        for nested in child:
            visit(nested)
        if child.tag == W + "p":
            pieces.append("\n")

    visit(element)
    return "".join(pieces).rstrip("\n")


def extract(data, filename):
    if not data or len(data) > MAX_UPLOAD:
        raise ValueError("Word文件须大于0字节且不超过16MB")
    if (
        not filename
        or len(filename) > 255
        or "/" in filename
        or "\\" in filename
        or any(ord(c) < 32 for c in filename)
        or not filename.lower().endswith(".docx")
    ):
        raise ValueError("请上传有效的.docx文件")
    try:
        with ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            names = {e.filename for e in entries}
            if (
                len(entries) > 5000
                or len(names) != len(entries)
                or sum(e.file_size for e in entries) > 128 * 1024 * 1024
                or any(e.flag_bits & 1 for e in entries)
                or any(
                    PurePosixPath(n).is_absolute() or ".." in PurePosixPath(n).parts or "\\" in n
                    for n in names
                )
                or any(n.lower().endswith("vbaproject.bin") for n in names)
            ):
                raise ValueError("Word压缩结构不受支持或超过解析限额")

            def xml(name, optional=False):
                if optional and name not in names:
                    return ElementTree.fromstring(b"<empty/>")
                if archive.getinfo(name).file_size > 32 * 1024 * 1024:
                    raise ValueError("Word XML超过解析限额")
                return ElementTree.fromstring(archive.read(name), forbid_dtd=True)

            document = xml("word/document.xml")
            styles = xml("word/styles.xml", optional=True)
    except (BadZipFile, KeyError, NotImplementedError, RuntimeError, zlib.error) as exc:
        raise ValueError("不是有效的Word文档") from exc
    body = document.find(W + "body")
    if body is None:
        raise ValueError("Word文档没有可读取的正文")
    style_map = {s.get(W + "styleId"): s for s in styles}

    def level(properties, seen=None):
        if properties is None:
            return None
        direct = properties.find(W + "outlineLvl")
        if direct is not None:
            value = int(direct.get(W + "val", "9"))
            return value + 1 if 0 <= value < 9 else None
        reference = properties.find(W + "pStyle")
        key = reference.get(W + "val") if reference is not None else None
        visited = set(seen or ())
        while key and key not in visited:
            visited.add(key)
            style = style_map.get(key)
            if style is None:
                break
            found = level(style.find(W + "pPr"), visited)
            if found:
                return found
            name = style.find(W + "name")
            match = re.fullmatch(
                r"(?:heading|标题)\s*([1-9])",
                name.get(W + "val", "") if name is not None else "",
                re.IGNORECASE,
            )
            if match:
                return int(match[1])
            parent = style.find(W + "basedOn")
            key = parent.get(W + "val") if parent is not None else None
        return None

    paragraphs = {id(p): i for i, p in enumerate(body.iter(W + "p"), 1)}
    lines, locations = [], []
    counts = {"tables": 0, "headings": 0, "unsupported_blocks": 0, "deep_headings": 0}

    def emit(value, location, heading=False):
        start = len(lines) + 1
        if not heading:
            fence = "`" * (max([2, *(len(s) for s in re.findall(r"`+", value))]) + 1)
            value = fence + "text\n" + value + "\n" + fence
        lines.extend((value + "\n\n").splitlines(keepends=True))
        locations.append({"start_line": start, "end_line": len(lines), **location})

    def walk(parent):
        for child in parent:
            if child.tag == W + "p":
                content = text_of(child)
                if not content.strip():
                    continue
                heading = level(child.find(W + "pPr"))
                if heading:
                    counts["headings"] += 1
                    counts["deep_headings"] += int(heading > 6)
                    content = " ".join(content.splitlines())
                emit(
                    ("#" * min(heading, 6) + " " if heading else "") + content,
                    {"kind": "paragraph", "paragraph": paragraphs[id(child)]},
                    bool(heading),
                )
            elif child.tag == W + "tbl":
                counts["tables"] += 1
                table = counts["tables"]
                for row_number, row in enumerate(child.findall(W + "tr"), 1):
                    cells = []
                    grid = 1
                    before = row.find(W + "trPr/" + W + "gridBefore")
                    if before is not None:
                        grid += int(before.get(W + "val", "0"))
                    for index, cell in enumerate(row.findall(W + "tc"), 1):
                        span = cell.find(W + "tcPr/" + W + "gridSpan")
                        merge = cell.find(W + "tcPr/" + W + "vMerge")
                        width = int(span.get(W + "val", "1")) if span is not None else 1
                        if not 1 <= width <= 1024 or not 1 <= grid <= 1024:
                            raise ValueError("表格列数超出解析限额")
                        cells.append(
                            {
                                "cell": index,
                                "column": grid,
                                "span": width,
                                "vertical_merge": merge.get(W + "val", "continue")
                                if merge is not None
                                else None,
                                "text": text_of(cell),
                            }
                        )
                        grid += width
                    emit(
                        f"表{table} 第{row_number}行 " + json.dumps(cells, ensure_ascii=False),
                        {"kind": "table_row", "table": table, "row": row_number},
                    )
            elif child.tag in {W + "sdt", W + "ins", W + "moveTo"}:
                wrapper = child.find(W + "sdtContent") if child.tag == W + "sdt" else child
                if wrapper is not None:
                    walk(wrapper)
            elif child.tag not in {
                W + tag
                for tag in (
                    "sectPr",
                    "del",
                    "moveFrom",
                    "bookmarkStart",
                    "bookmarkEnd",
                    "proofErr",
                    "permStart",
                    "permEnd",
                    "commentRangeStart",
                    "commentRangeEnd",
                )
            }:
                counts["unsupported_blocks"] += 1

    walk(body)
    text = "".join(lines)
    if len(text.encode("utf8")) > MAX_BYTES:
        raise ValueError("提取正文超过限额，请先将Word按完整章节拆分")
    _, _, sections = DocumentReader.parse(text.encode("utf8"), filename)
    if not sections or len(sections) > 2000:
        raise ValueError("没有可读取的文字章节，或章节超过2000个")
    counts.update(
        {
            "paragraphs": len(paragraphs),
            "images": sum(1 for tag in ("drawing", "pict") for _ in body.iter(W + tag)),
            "objects": sum(1 for _ in body.iter(W + "object")),
            "merged_cells": sum(1 for tag in ("vMerge", "gridSpan") for _ in body.iter(W + tag)),
            "numbering": sum(1 for root in (body, styles) for _ in root.iter(W + "numPr")),
            "tracked_changes": sum(
                1 for tag in ("del", "ins", "moveFrom", "moveTo") for _ in body.iter(W + tag)
            ),
            "nested_tables": sum(1 for _ in body.iter(W + "tbl")) - counts["tables"],
            "ancillary_parts": sum(
                bool(re.match(r"word/(header|footer|footnotes|endnotes|comments).*xml$", n))
                for n in names
            ),
        }
    )
    warnings = ["按文字结构提取，不重建页码或版式；引用位置使用段落、表行与提取行号。"]
    for field, message in {
        "images": "图片、拓扑图和图中文字未提取",
        "objects": "嵌入对象未提取",
        "merged_cells": "表格保留行列与合并标记，合并单元格的业务含义需核对原文",
        "numbering": "自动编号未还原，请核对原文中的步骤和条目号",
        "tracked_changes": "存在修订，按接受插入、排除删除后的文字提取",
        "nested_tables": "嵌套表格按单元格文字读取，内层布局未还原",
        "ancillary_parts": "页眉、页脚、脚注、尾注或批注未提取",
        "unsupported_blocks": "存在未支持的正文对象，请核对原文件",
        "deep_headings": "超过六级的标题层级被压平",
    }.items():
        if counts[field]:
            warnings.append(f"{message}（{counts[field]}处）")
    return {
        "filename": filename,
        "source_sha256": digest(data),
        "normalized_sha256": digest(text),
        "text": text,
        "sections": [asdict(s) for s in sections],
        "locations": locations,
        "warnings": warnings,
        "counts": counts,
        "status": "text_only_requires_review",
    }
