"""Immutable per-turn files for DSH's native glob/grep/read, with source mappings."""

import json
import re
from pathlib import Path

from src.apps.materials.backend.documents import FILTERS, digest
from src.apps.materials.backend.library import LibraryReader
from src.platform.authorization.client import authorize_worker
from src.platform.tools import registry

from . import answer_gate, evidence, tool_trace


class DocumentWorkspace:
    def __init__(self, actor_id, *, reader=None, filters=None, check=None):
        self.actor_id = actor_id
        self.reader = reader if reader is not None else LibraryReader()
        self.filters = filters or {}
        if set(self.filters) - FILTERS:
            raise ValueError("Unsupported document filters")
        self.check = check or (lambda: authorize_worker(actor_id, "assistant"))
        self.files = {}
        self.observed = {}
        self.business_evidence = {}
        self.evidence_tools = set()
        self.root = None
        self.prepared_answer = None
        self.validation_issue = None

    def prepare(self, root):
        self.check()
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=False)
        catalog, size = [], 0
        for doc in self.reader.document_ids():
            revision, metadata, sections = self.reader.load(doc)
            if any(metadata.get(k, "").casefold() != v.casefold() for k, v in self.filters.items()):
                continue
            # File paths are generated from revision identity, never supplied by a model.
            prefix = digest(doc + ":" + revision)[:20]
            for section in sections:
                relative = f"{prefix}/{section.id}.txt"
                raw = section.text.encode("utf-8")
                size += len(raw)
                if size > 40 * 1024 * 1024 or len(self.files) >= 2000:
                    raise ValueError("Document scope too large; narrow the version/product filter")
                path = self.root / relative
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(raw)
                self.files[relative] = (doc, revision, section)
                catalog.append(
                    {"file": relative, "title": section.title_path, "metadata": metadata}
                )
        (self.root / "CATALOG.md").write_text(
            "# 资料目录\n" + "\n".join(json.dumps(row, ensure_ascii=False) for row in catalog),
            encoding="utf-8",
        )
        self.check()

    def definitions(self):
        self.check()
        # Initial diagnosis queries existing facts; fixed business operations remain in their Apps.
        definitions = registry.available_tools(
            actor_id=self.actor_id, read_only=True, evidence_only=True
        )
        self.evidence_tools = {t["name"] for t in definitions}
        return definitions

    def describe_tool(self, name, arguments):
        """Resolve a human-readable title independently from the bounded tool trace."""
        if name != "read" or not self.root:
            return ""

        try:
            path = (self.root / arguments["file_path"]).resolve()
            relative = path.relative_to(self.root).as_posix()
            if relative == "CATALOG.md":
                return "资料目录"
            section = self.files[relative][2]
            return " / ".join(section.title_path)[:200]
        except (KeyError, ValueError, TypeError, OSError):
            return ""

    def tool_operation(self, name, arguments):
        return tool_trace.operation(self.root, name, arguments)

    def submit_answer(self, arguments):
        return answer_gate.submit(self, arguments)

    def invoke(self, name, arguments):
        self.check()
        if name not in self.evidence_tools:
            raise PermissionError("Tool is outside this diagnosis contract")
        result = registry.invoke(name, arguments, actor_id=self.actor_id, read_only=True)
        reference = evidence.capture(name, result)
        self.business_evidence[reference["id"]] = reference
        return {
            "data": result,
            "citation": reference["citation"],
            "retrieved_at": reference["retrieved_at"],
            "notice": "引用仅证明本次读取的记录；按采集时间和质量状态判读，不代表实时采集",
        }

    def record_read(self, value):
        self.check()
        path = Path(value["path"]).resolve()
        if not path.is_relative_to(self.root):
            raise PermissionError("Read outside document workspace")
        relative = path.relative_to(self.root).as_posix()
        if relative == "CATALOG.md":
            return
        _, _, section = self.files[relative]
        source = section.text.splitlines()
        lines = self.observed.setdefault(relative, set())
        for row in value["lines"]:
            number = row["number"]
            # Truncated lines cannot stand in for the full original evidence.
            if 1 <= number <= len(source) and row["text"] == source[number - 1]:
                lines.add(number)

    def citation_guidance(self):
        """Compact location metadata only; never promote grep/catalog to read evidence."""
        markers = []
        for filename, numbers in sorted(self.observed.items()):
            ordered = sorted(numbers)
            if not ordered:
                continue
            source = self.files[filename][2].text.splitlines(keepends=True)
            start = end = ordered[0]
            size = len(source[start - 1])
            for number in ordered[1:]:
                length = len(source[number - 1])
                if number != end + 1 or number - start >= 200 or size + length > 8000:
                    markers.append(f"[{filename}:L{start}-L{end}]")
                    start = number
                    size = 0
                size += length
                end = number
            markers.append(f"[{filename}:L{start}-L{end}]")
        markers.extend(f"[{identifier}]" for identifier in sorted(self.business_evidence))
        return (
            "服务端本轮读取记录（仅定位信息，不是原文）：\n"
            + ("\n".join(markers[:64]) if markers else "尚未读取任何可引用证据。")
            + "\n只能引用本轮已读范围内的必要行，行号包含边界；可取其中的子区间，不能跨过未读行。"
            "需要引用其他版本、文件或范围时请先调用read读取正文；不需要的比较或旁注可省略。"
            "CATALOG.md和grep搜索结果不能作为引用；历史回答中的引用也不代表本轮已读。"
            "没有正文支持时明确资料不足，不为拒答附加目录引用。"
        )

    def references(self, answer):
        self.check()
        citations = []
        pattern = r"\[([a-f0-9]{20}/line-[1-9][0-9]*\.txt):L([1-9][0-9]*)(?:-L?([1-9][0-9]*))?\]"
        # Reject malformed file references too, including invented/unread filenames.
        for candidate in re.findall(r"\[[^\]\n]*:L[^\]\n]*\]", answer):
            if not re.fullmatch(pattern, candidate):
                raise ValueError("Malformed source reference")
        for match in re.finditer(pattern, answer):
            filename, start = match.group(1), int(match.group(2))
            end = int(match.group(3) or match.group(2))
            if end < start or end - start >= 200:
                raise ValueError("Invalid source line range")
            if not set(range(start, end + 1)) <= self.observed.get(filename, set()):
                raise ValueError("Answer cites unread lines")
            doc, revision, section = self.files[filename]
            lines = section.text.splitlines(keepends=True)
            offset = sum(map(len, lines[: start - 1]))
            length = sum(map(len, lines[start - 1 : end]))
            if not 0 < length <= 8000:
                raise ValueError("Citation exceeds read budget")
            citation = self.reader.read_section(
                doc, revision, section.id, offset=offset, limit=length
            )["citation"]
            self.reader.validate_citation(citation)
            if citation not in citations:
                citations.append(citation)
        document_citations = len(citations)
        for identifier in re.findall(r"\[(evidence-[^\]\n]*)\]", answer):
            if identifier not in self.business_evidence:
                raise ValueError("Answer cites unread business evidence")
            reference = evidence.validate(self.business_evidence[identifier])
            for app_id in evidence.source_apps(reference):
                authorize_worker(self.actor_id, app_id)
            if reference not in citations:
                citations.append(reference)
        # Every evidence type actually used must have an accessible source attached.
        if self.observed and not document_citations:
            raise ValueError("Read sources were not cited")
        if self.business_evidence and len(citations) == document_citations:
            raise ValueError("Business observations were not cited")
        return citations
