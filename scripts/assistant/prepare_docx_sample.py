"""Offline DOCX structural sample; never imports or invokes a model client.

Outputs stay under .runtime/assistant-eval, outside the live knowledge library.
Only aggregate statistics are printed. The source is read, never modified.
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def text_of(element):
    pieces = []

    def visit(child):
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


def prepare(source, *, vendor, product, version):
    source = Path(source).resolve(strict=True)
    hasher = hashlib.sha256()
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    original_hash = hasher.hexdigest()
    output = ROOT / ".runtime" / "assistant-eval" / original_hash[:16]
    output.mkdir(parents=True, exist_ok=True)
    with ZipFile(source) as archive:
        entries = {entry.filename: entry for entry in archive.infolist()}
        for name in ("word/document.xml", "word/styles.xml"):
            if entries[name].file_size > 64 * 1024 * 1024:
                raise ValueError("DOCX XML exceeds the local extraction budget")
        document = ET.fromstring(archive.read("word/document.xml"))
        styles = ET.fromstring(archive.read("word/styles.xml"))
        ancillary = sum(
            bool(re.match(r"word/(header|footer|footnotes|endnotes).*xml$", n)) for n in entries
        )
    style_map = {s.get(W + "styleId"): s for s in styles}

    def outline(properties, seen=None):
        if properties is None:
            return None
        level = properties.find(W + "outlineLvl")
        if level is not None:
            value = int(level.get(W + "val", "9"))
            return value + 1 if value < 9 else None
        reference = properties.find(W + "pStyle")
        key = reference.get(W + "val") if reference is not None else None
        visited = set(seen or ())
        while key and key not in visited:
            visited.add(key)
            style = style_map.get(key)
            if style is None:
                break
            found = outline(style.find(W + "pPr"), visited)
            if found is not None:
                return found
            name = style.find(W + "name")
            match = re.fullmatch(
                r"(?:heading|标题)\s*([1-9])",
                (name.get(W + "val", "") if name is not None else ""),
                re.IGNORECASE,
            )
            if match:
                return int(match[1])
            parent = style.find(W + "basedOn")
            key = parent.get(W + "val") if parent is not None else None
        return None

    body = document.find(W + "body")
    paragraphs = {id(p): i for i, p in enumerate(body.iter(W + "p"), 1)}
    lines = [
        "---\n",
        *(
            f"{key}: {json.dumps(value, ensure_ascii=False)}\n"
            for key, value in {
                "vendor": vendor,
                "product": product,
                "software_version": version,
                "doc_type": "configuration",
                "source_sha256": original_hash,
                "extraction_status": "text_only_requires_review",
            }.items()
        ),
        "---\n",
    ]
    locations, table_count, heading_count, unknown = [], 0, 0, 0

    def emit(value, *, heading=False, **location):
        start = len(lines) + 1
        if not heading:
            # CLI output may itself start with '#'. Preserve it literally rather
            # than letting the Markdown reader invent DOCX headings.
            fence = "`" * (max([2, *(len(s) for s in re.findall(r"`+", value))]) + 1)
            value = fence + "text\n" + value + "\n" + fence
        lines.extend((value + "\n\n").splitlines(keepends=True))
        locations.append({"start_line": start, "end_line": len(lines), **location})

    def walk(parent):
        nonlocal table_count, heading_count, unknown
        for child in parent:
            if child.tag == W + "p":
                content = text_of(child)
                if content.strip():
                    level = outline(child.find(W + "pPr"))
                    if level:
                        heading_count += 1
                    emit(
                        ("#" * min(level, 6) + " " if level else "") + content,
                        heading=bool(level),
                        kind="paragraph",
                        paragraph=paragraphs[id(child)],
                    )
            elif child.tag == W + "tbl":
                table_count += 1
                for row_index, row in enumerate(child.findall(W + "tr"), 1):
                    # Preserve physical cells, including empty/merged cells, without
                    # inventing column headers or silently propagating merged values.
                    for cell_index, cell in enumerate(row.findall(W + "tc"), 1):
                        emit(
                            text_of(cell),
                            kind="table_cell",
                            table=table_count,
                            row=row_index,
                            cell=cell_index,
                        )
            elif child.tag == W + "sdt":
                content = child.find(W + "sdtContent")
                if content is not None:
                    walk(content)
            elif child.tag != W + "sectPr":
                unknown += 1

    walk(body)
    normalized = "".join(lines)
    target = output / "sample.md"
    target.write_text(normalized, encoding="utf-8", newline="")
    counts = {
        "source_bytes": source.stat().st_size,
        "paragraphs": len(paragraphs),
        "headings": heading_count,
        "tables": table_count,
        "normalized_characters": len(normalized),
        "source_locations": len(locations),
        "drawings": len(list(body.iter(W + "drawing"))),
        "legacy_pictures": len(list(body.iter(W + "pict"))),
        "merged_cells": len(list(body.iter(W + "vMerge"))) + len(list(body.iter(W + "gridSpan"))),
        "numbered_paragraphs": len(list(body.iter(W + "numPr"))),
        "ancillary_parts": ancillary,
        "unknown_blocks": unknown,
        "tracked_deletions": len(list(body.iter(W + "del"))),
    }
    manifest = {
        "source": str(source),
        "source_sha256": original_hash,
        "normalized_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "status": "text_only_requires_review",
        "model_transmission": "not_authorized",
        "model_calls": 0,
        "limitations": [
            "Images/diagrams are not OCRed",
            "No page-number reconstruction",
            "Headers/footnotes/endnotes are not extracted",
            "Automatic numbering and merged-cell meaning need review",
            "Table cells are preserved in physical order, not layout-rendered",
        ],
        "counts": counts,
        "locations": locations,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return output, counts


def verify(output):
    sys.path.insert(0, str(ROOT))
    from src.apps.materials.backend.documents import DocumentReader

    reader = DocumentReader(output)
    revision, _, sections = reader.load("sample.md")
    pages = 0
    for section in sections:
        pieces, offset = [], 0
        while offset is not None:
            page = reader.read_section("sample.md", revision, section.id, offset=offset)
            pieces.append(page["text"])
            offset = page["next_offset"]
            pages += 1
        if "".join(pieces) != section.text:
            raise AssertionError("Section pagination mismatch")
    # Validate selected revision-bound citations, without logging their contents.
    for section in sections[:: max(1, len(sections) // 10)]:
        page = reader.read_section("sample.md", revision, section.id)
        reader.validate_citation(page["citation"])
    return {
        "sections": len(sections),
        "pages": pages,
        "pagination_roundtrip": True,
        "citation_validation": True,
        "model_calls": 0,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--vendor", required=True)
    parser.add_argument("--product", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Extract locally without loading application dependencies",
    )
    args = parser.parse_args()
    directory, statistics = prepare(
        args.source, vendor=args.vendor, product=args.product, version=args.version
    )
    result = {"counts": statistics, "output_directory": str(directory), "model_calls": 0}
    if not args.prepare_only:
        result["verification"] = verify(directory)
        (directory / "verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
