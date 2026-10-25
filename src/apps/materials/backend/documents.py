"""Read shared curated text sources with revision-bound, paginated evidence.

This first slice supports Markdown/text only. It does not silently convert PDF,
Word or spreadsheets, or read arbitrary user files.
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml

MAX_BYTES = 2 * 1024 * 1024
FILTERS = {"vendor", "product", "software_version", "doc_type", "category"}


def digest(value: str | bytes) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def terms(text):
    """Identical command tokens and Chinese bigrams for queries and documents."""
    result = set(re.findall(r"[a-z0-9][a-z0-9_.:/-]*", text.lower()))
    for phrase in re.findall(r"[\u4e00-\u9fff]+", text):
        result.update(phrase[i : i + 2] for i in range(max(1, len(phrase) - 1)))
    return result


@dataclass(frozen=True)
class Section:
    id: str
    title_path: tuple[str, ...]
    start_line: int
    text: str


class DocumentReader:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def load(self, document_id, revision=None):
        relative = PurePosixPath(document_id)
        if not document_id or "\\" in document_id or ":" in document_id or relative.is_absolute():
            raise ValueError("invalid_document_id")
        if ".." in relative.parts:
            raise ValueError("invalid_document_id")
        path = (self.root / document_id).resolve()
        if not path.is_relative_to(self.root) or not path.is_file():
            raise ValueError("document_unavailable")
        if path.suffix.lower() not in {".md", ".txt"}:
            raise ValueError("unsupported_document_format")
        with path.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("document_too_large")
        version = digest(raw)
        if revision is not None and revision != version:
            raise ValueError("document_revision_changed")
        return self.parse(raw, path.name)

    @staticmethod
    def parse(raw, filename):
        version = digest(raw)
        path = Path(filename)
        lines = raw.decode("utf-8-sig", errors="strict").splitlines(keepends=True)
        metadata, begin = {}, 0
        if lines and lines[0].strip() == "---":
            end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
            if end is None:
                raise ValueError("invalid_document_metadata")
            parsed = yaml.safe_load("".join(lines[1:end])) or {}
            if not isinstance(parsed, dict):
                raise ValueError("invalid_document_metadata")
            metadata = {k: str(v) for k, v in parsed.items() if k in FILTERS and v is not None}
            begin = end + 1
        # Match the existing curated-library metadata vocabulary.
        if "doc_type" not in metadata:
            metadata["doc_type"] = (
                "sop"
                if "sop" in path.name.lower()
                else ("configuration" if "配置" in path.name else "general")
            )
        if "category" not in metadata:
            metadata["category"] = next(
                (
                    kind
                    for word, kind in (
                        ("交换机", "switch"),
                        ("防火墙", "firewall"),
                        ("Linux", "linux"),
                        ("路由器", "router"),
                    )
                    if word in path.name
                ),
                "general",
            )
        starts, titles, fence = [begin], [()], None
        headings = []
        for i in range(begin, len(lines)):
            line = lines[i]
            marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
            if marker:
                token = marker[1]
                if fence is None:
                    fence = token
                elif token[0] == fence[0] and len(token) >= len(fence):
                    fence = None
                continue
            heading = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line) if fence is None else None
            if heading:
                level, title = len(heading[1]), heading[2]
                headings = [(n, text) for n, text in headings if n < level] + [(level, title)]
                starts.append(i)
                titles.append(tuple(text for _, text in headings))
        sections = []
        for index, start in enumerate(starts):
            end = starts[index + 1] if index + 1 < len(starts) else len(lines)
            text = "".join(lines[start:end])
            if text.strip():
                sections.append(Section(f"line-{start + 1}", titles[index], start + 1, text))
        return version, metadata, sections

    def document_ids(self):
        return [
            p.relative_to(self.root).as_posix() for p in sorted(self.root.rglob("*")) if p.is_file()
        ]

    def search(self, query, *, filters=None, limit=8):
        filters = filters or {}
        if set(filters) - FILTERS or any(not isinstance(v, str) for v in filters.values()):
            raise ValueError("unsupported_metadata_filter")
        tokens = terms(query)
        hits, skipped = [], []
        for name in self.document_ids():
            try:
                version, meta, sections = self.load(name)
            except (ValueError, OSError, UnicodeError, yaml.YAMLError):
                skipped.append({"document_id": name, "status": "not_indexed"})
                continue
            if any(meta.get(k, "").casefold() != v.casefold() for k, v in filters.items()):
                continue
            for section in sections:
                title = " / ".join(section.title_path) or name
                score = len(tokens & terms(section.text)) + 3 * len(tokens & terms(title))
                if not score:
                    continue
                hits.append(
                    {
                        "document_id": name,
                        "revision": version,
                        "section_id": section.id,
                        "title": title,
                        "start_line": section.start_line,
                        "metadata": meta,
                        "score": score,
                        "preview": section.text[:200],
                    }
                )
        hits.sort(key=lambda row: (-row["score"], row["document_id"], row["start_line"]))
        return {
            "items": hits[: max(1, min(limit, 20))],
            "total": len(hits),
            "skipped": skipped[:50],
        }

    def list_sections(self, document_id, revision, *, offset=0, limit=20):
        if offset < 0 or not 1 <= limit <= 50:
            raise ValueError("invalid_section_page")
        _, _, sections = self.load(document_id, revision)
        page = sections[offset : offset + limit]
        return {
            "items": [
                {
                    "section_id": s.id,
                    "title_path": s.title_path,
                    "start_line": s.start_line,
                    "length": len(s.text),
                }
                for s in page
            ],
            "next_offset": offset + len(page) if offset + len(page) < len(sections) else None,
        }

    def read_section(self, document_id, revision, section_id, *, offset=0, limit=4000):
        _, _, sections = self.load(document_id, revision)
        section = next((s for s in sections if s.id == section_id), None)
        if section is None or not 0 <= offset < len(section.text) or not 1 <= limit <= 8000:
            raise ValueError("section_or_offset_unavailable")
        excerpt = section.text[offset : offset + limit]
        end = offset + len(excerpt)
        start_line = section.start_line + section.text[:offset].count("\n")
        citation = {
            "id": "doc-" + digest(f"{document_id}|{revision}|{section_id}|{offset}|{end}")[:20],
            "document_id": document_id,
            "revision": revision,
            "section_id": section_id,
            "title_path": list(section.title_path),
            "offset": offset,
            "end_offset": end,
            "start_line": start_line,
            "end_line": start_line + excerpt.rstrip("\n").count("\n"),
            "excerpt_sha256": digest(excerpt),
            "excerpt": excerpt,
        }
        return {
            "text": excerpt,
            "citation": citation,
            "next_offset": end if end < len(section.text) else None,
        }

    def validate_citation(self, citation):
        current = self.read_section(
            citation["document_id"],
            citation["revision"],
            citation["section_id"],
            offset=citation["offset"],
            limit=citation["end_offset"] - citation["offset"],
        )
        if current["citation"] != citation:
            raise ValueError("citation_mismatch")
