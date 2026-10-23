"""Immutable per-turn files for DSH's native glob/grep/read, with source mappings."""

import json
import re
from pathlib import Path

from src.apps.materials.backend.documents import FILTERS, digest
from src.apps.materials.backend.library import LibraryReader
from src.platform.authorization.client import authorize_worker
from src.platform.clock import in_beijing
from src.platform.tools import registry

from . import answer_gate, evidence, tool_trace

# Text attachments sit beside the document sections; both are cited as [file:Lx-Ly].
ATTACHMENT_PATH = "attachments/{}.txt"
CITATION = (
    r"\[((?:[a-f0-9]{20}/line-[1-9][0-9]*|attachments/[a-f0-9]{12})\.txt)"
    r":L([1-9][0-9]*)(?:-L?([1-9][0-9]*))?\]"
)


def attachment_citation(item, filename, start, end, excerpt):
    """A cited range of a user's attachment, kept with the answer like a document excerpt."""
    return {
        "kind": "attachment",
        "id": "attachment-" + digest(f"{item['file_id']}:{start}:{end}:{excerpt}")[:24],
        "file_id": item["file_id"],
        "title_path": ["用户附件", item["name"]],
        "revision": digest(excerpt),
        "excerpt": excerpt,
        "lines": [start, end],
        "citation": f"[{filename}:L{start}-L{end}]",
    }


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
        # Evidence earlier answers in this conversation showed: citable, never required.
        self.earlier_evidence = {}
        self.evidence_tools = set()
        self.root = None
        self.prepared_answer = None
        self.validation_issue = None
        # Files attached in this conversation (attachments.load); text ones are readable here.
        self.attachments = []
        self.attachment_files = {}

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
        for relative, item in self.attachment_entries():
            path = self.root / relative
            path.parent.mkdir(exist_ok=True)
            path.write_text(item["text"], encoding="utf-8", newline="\n")
            self.attachment_files[relative] = item
        self.check()

    def attachment_entries(self):
        """(workspace path, attachment) for each text attachment, paths from file identity."""
        return [
            (ATTACHMENT_PATH.format(digest(item["file_id"])[:12]), item)
            for item in self.attachments if "text" in item
        ]

    def attachment_paths(self):
        """Where each text attachment is readable in the turn's workspace, by file id."""
        return {item["file_id"]: relative for relative, item in self.attachment_entries()}

    def source_lines(self, relative, keepends=False):
        """Lines of a workspace file the model may cite: a document section or an attachment."""
        attached = self.attachment_files.get(relative)
        text = attached["text"] if attached is not None else self.files[relative][2].text
        return text.splitlines(keepends=keepends)

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
            if relative in self.attachment_files:
                return "用户附件 / " + self.attachment_files[relative]["name"]
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
        result = in_beijing(
            registry.invoke(name, arguments, actor_id=self.actor_id, read_only=True)
        )
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
        source = self.source_lines(relative)
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
            source = self.source_lines(filename, keepends=True)
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
        earlier = [
            f"[{identifier}]"
            for identifier in sorted(self.earlier_evidence)
            if identifier not in self.business_evidence
        ]
        return (
            "服务端本轮读取记录（仅定位信息，不是原文）：\n"
            + ("\n".join(markers[:64]) if markers else "尚未读取任何可引用证据。")
            + (
                "\n本对话之前的回答取得的业务证据，也可引用（按它当时的采集时间表述）：\n"
                + "\n".join(earlier[:64])
                if earlier
                else ""
            )
            + "\n只能引用本轮已读范围内的必要行，行号包含边界；可取其中的子区间，不能跨过未读行。"
            "需要引用其他版本、文件或范围时请先调用read读取正文；不需要的比较或旁注可省略。"
            "CATALOG.md和grep搜索结果不能作为引用；历史回答中的引用也不代表本轮已读。"
            "没有正文支持时明确资料不足，不为拒答附加目录引用。"
        )

    def references(self, answer):
        self.check()
        citations = []
        pattern = CITATION
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
            lines = self.source_lines(filename, keepends=True)
            offset = sum(map(len, lines[: start - 1]))
            length = sum(map(len, lines[start - 1 : end]))
            if not 0 < length <= 8000:
                raise ValueError("Citation exceeds read budget")
            if filename in self.attachment_files:
                citation = attachment_citation(
                    self.attachment_files[filename], filename, start, end,
                    "".join(lines[start - 1 : end]),
                )
                if citation not in citations:
                    citations.append(citation)
                continue
            doc, revision, section = self.files[filename]
            citation = self.reader.read_section(
                doc, revision, section.id, offset=offset, limit=length
            )["citation"]
            self.reader.validate_citation(citation)
            if citation not in citations:
                citations.append(citation)
        document_citations, this_turn = len(citations), False
        for identifier in re.findall(r"\[(evidence-[^\]\n]*)\]", answer):
            found = self.business_evidence.get(identifier) or self.earlier_evidence.get(identifier)
            if found is None:
                raise ValueError("Answer cites unread business evidence")
            this_turn = this_turn or identifier in self.business_evidence
            reference = evidence.validate(found)
            for app_id in evidence.source_apps(reference):
                authorize_worker(self.actor_id, app_id)
            if reference not in citations:
                citations.append(reference)
        # Every evidence type actually used must have an accessible source attached.
        if self.observed and not document_citations:
            raise ValueError("Read sources were not cited")
        if self.business_evidence and not this_turn:
            raise ValueError("Business observations were not cited")
        return citations

    def unknown_evidence(self, answer):
        """Cited brackets that name no evidence of this turn or this conversation."""
        known = self.business_evidence.keys() | self.earlier_evidence.keys()
        return [
            f"[{identifier}]"
            for identifier in dict.fromkeys(re.findall(r"\[(evidence-[^\]\n]*)\]", answer))
            if identifier not in known
        ]
