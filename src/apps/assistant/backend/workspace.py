"""Immutable per-turn files for DSH's native glob/grep/read, with source mappings."""

import json
import os
import re
from pathlib import Path

from src.apps.materials.backend import docset_cache, figure_reading
from src.apps.materials.backend import docsets as docset_store
from src.apps.materials.backend.documents import FILTERS, digest
from src.apps.materials.backend.library import LibraryReader
from src.platform.authorization.client import authorize_worker
from src.platform.clock import in_beijing
from src.platform.tools import registry

from . import answer_gate, docset_scope, evidence, tool_trace

# Text attachments sit beside the document sections; both are cited as [file:Lx-Ly].
ATTACHMENT_PATH = "attachments/{}.txt"
# Doc-set pages are cited by their path in the linked doc-set directory.
DOCSET_PAGE = r"[a-f0-9]{20}/pages/[^\s\[\]:]+?\.md"
CITATION = (
    r"\[((?:[a-f0-9]{20}/line-[1-9][0-9]*|attachments/[a-f0-9]{12})\.txt|" + DOCSET_PAGE + ")"
    r":L([1-9][0-9]*)(?:-L?([1-9][0-9]*))?\]"
)
OPEN_DOCSET = "open_docset"
READING_SKILL = Path(docset_cache.__file__).resolve().parents[1] / "skills" / "product-docs"
OPEN_DEFINITION = {
    "name": OPEN_DOCSET,
    "description": (
        "展开 DOCSETS.md 里列出的一个产品文档集，返回它在工作区的目录名；"
        "之后用 grep/read 读取该目录。每轮最多展开 3 个。"
    ),
    "input_schema": {
        "type": "object",
        # The ID from DOCSETS.md; the 20-character directory name of an open one also works.
        "properties": {
            "docset_id": {"type": "string", "pattern": "^[0-9a-f]{20}(?:[0-9a-f]{12})?$"}
        },
        "required": ["docset_id"],
        "additionalProperties": False,
    },
}
# A figure is cited by its name under the doc-set directory, after view_figure read it.
FIGURE_NAME = r"figures/[0-9a-f]{64}\.(?:png|gif|jpg|bmp)"
DOCSET_FIGURE = r"\[([a-f0-9]{20})/(" + FIGURE_NAME + r")\]"
VIEW_FIGURE = "view_figure"
FIGURE_LIMIT = 4
VIEW_DEFINITION = {
    "name": VIEW_FIGURE,
    "description": (
        "识读产品文档页面里的一张插图（组网图、拓扑图），返回视觉模型对图中文字、设备、连线和地址的"
        "转写，不是手册原文。先 read 页面里这张图所在的行；每轮最多 4 张。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "page": {"type": "string", "description": "本轮读过的页面，如 <目录>/pages/x.md"},
            "figure": {
                "type": "string",
                "pattern": "^" + FIGURE_NAME + "$",
                "description": "页面里的图片路径，如 figures/<sha>.png",
            },
        },
        "required": ["page", "figure"],
        "additionalProperties": False,
    },
}


def resolved(path):
    """Path.resolve, without the \\\\?\\ prefix Windows keeps on results longer than MAX_PATH."""
    text = str(Path(path).resolve())
    if text.startswith("\\\\?\\UNC\\"):
        return Path("\\\\" + text[8:])
    return Path(text.removeprefix("\\\\?\\"))


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
    def __init__(self, actor_id, *, reader=None, filters=None, check=None, docsets=None,
                 question=""):
        self.actor_id = actor_id
        # Product doc sets: chosen by docset_scope rules, linked (never copied) per turn.
        self.picked_docsets = list(docsets or [])
        self.earlier_docsets = []
        self.question = question
        self.docset_catalog = []
        self.mounted = {}  # directory name in the workspace -> doc set and its cache
        self.docset_lines = {}
        self.figure_readings = {}  # "<directory>/figures/<name>" -> its citation, this turn
        self.skills_dir = None
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
        self.prepare_docsets()
        self.check()

    def prepare_docsets(self):
        self.docset_catalog = docset_store.catalog()
        if not self.docset_catalog:
            return
        chosen = docset_scope.choose(
            self.docset_catalog, self.picked_docsets, self.earlier_docsets, self.question
        )
        for docset, reason in chosen:
            self.mount(docset, reason)
        self.write_docsets()

    def mount(self, docset, reason):
        """Link one doc set's cached revision into the workspace; its directory name."""
        prefix = digest(docset["id"] + ":" + docset["revision"])[:20]
        if prefix in self.mounted:
            return prefix
        if len(self.mounted) >= docset_scope.LIMIT:
            raise PermissionError(f"每轮最多展开 {docset_scope.LIMIT} 个文档集")
        target = docset_cache.ensure(docset["id"], docset["revision"])
        docset_cache.link(target, self.root / prefix)
        self.mounted[prefix] = {**docset, "path": target, "reason": reason}
        return prefix

    def write_docsets(self):
        """DOCSETS.md: what is opened this turn and what else the library holds."""
        rows = ["# 产品文档集", "",
                "本轮已展开。目录名是 read 路径的开头；grep 和 glob 要把 path 设成这个目录"
                "（或其下的 pages、index），从工作区根目录搜不到其中的文件。",
                "目录 | ID | 文档集 | 厂商 | 产品 | 软件版本 | 适用型号与别名 | 展开原因"]
        opened = {d["id"] for d in self.mounted.values()}
        for prefix, docset in self.mounted.items():
            meta = docset["metadata"]
            rows.append(" | ".join([
                prefix, docset["id"], docset["title"], meta.get("vendor", ""),
                meta.get("product", ""), meta.get("software_version", ""), meta.get("models", ""),
                docset_scope.REASONS[docset["reason"]],
            ]))
        if not self.mounted:
            rows.append("（无）")
        rows += ["", "资料库里的其他文档集（需要时调用 mcp__netops__open_docset 并填 ID）：",
                 "ID | 文档集 | 厂商 | 产品 | 软件版本 | 适用型号与别名"]
        others = [d for d in self.docset_catalog if d["id"] not in opened]
        for docset in others:
            meta = docset["metadata"]
            rows.append(" | ".join([
                docset["id"], docset["title"], meta.get("vendor", ""), meta.get("product", ""),
                meta.get("software_version", ""), meta.get("models", ""),
            ]))
        if not others:
            rows.append("（无）")
        text = "\n".join(rows) + "\n"
        (self.root / "DOCSETS.md").write_text(text, encoding="utf-8", newline="\n")

    def open_docset(self, arguments):
        self.check()
        if set(arguments) != {"docset_id"}:
            raise PermissionError("Only docset_id is accepted")
        opened = self.mounted.get(arguments["docset_id"])
        if opened is not None:  # Given the directory name of one already open.
            return {"directory": arguments["docset_id"], "docset": opened["title"],
                    "notice": "这个文档集本轮已经展开，直接按目录名读取。"}
        docset = next((d for d in self.docset_catalog if d["id"] == arguments["docset_id"]), None)
        if docset is None:
            raise PermissionError("DOCSETS.md 里没有这个文档集")
        if not self.cache_ready(docset):
            return {
                "directory": None,
                "notice": "该文档集正在准备（首次约需半分钟），本轮无法读取；请告诉用户稍后再问。",
            }
        prefix = self.mount(docset, "model")
        self.write_docsets()
        meta = docset["metadata"]
        return {
            "directory": prefix,
            "docset": docset["title"],
            "software_version": meta.get("software_version", ""),
            "notice": (
                f"已展开到目录 {prefix}；按 product-docs 技能的查法读取，"
                f"引用 {prefix}/pages/ 下读过的行。"
            ),
        }

    def view_figure(self, arguments):
        """A vision model's reading of one figure on a page whose figure line was read."""
        self.check()
        if set(arguments) != {"page", "figure"}:
            raise PermissionError("Only page and figure are accepted")
        figure = str(arguments["figure"])
        if not re.fullmatch(FIGURE_NAME, figure):
            raise PermissionError("figure 应是页面里的 figures/<sha>.<扩展名>")
        relative = self.workspace_path(str(arguments["page"]))
        page = self.docset_page(relative) if relative else None
        if page is None:
            raise PermissionError("page 应是本轮已展开文档集里的页面")
        prefix = relative.partition("/")[0]
        key = f"{prefix}/{figure}"
        if key in self.figure_readings:
            found = self.figure_readings[key]
            return {"figure": key, "citation": f"[{key}]", "reading": found["reading"]}
        lines = self.source_lines(relative) or []
        shown = {n for n, line in enumerate(lines, 1) if f"]({figure})" in line}
        if not shown:
            raise PermissionError("这张图不在该页面里")
        if not shown & self.observed.get(relative, set()):
            raise PermissionError(f"先 read 页面里这张图所在的行（第 {min(shown)} 行）")
        if len(self.figure_readings) >= FIGURE_LIMIT:
            raise PermissionError(f"每轮最多识读 {FIGURE_LIMIT} 张插图")
        docset, path = page
        sha, _, ext = figure.removeprefix("figures/").partition(".")
        try:
            citation = figure_reading.citation(docset["id"], docset["revision"], path, sha, ext)
        except ValueError as exc:
            raise PermissionError(
                "插图识读暂不可用；回答里说明结论依赖这张图，请用户在资料库打开原页面查看"
            ) from exc
        self.figure_readings[key] = citation
        return {
            "figure": key,
            "citation": f"[{key}]",
            "reading": citation["reading"],
            "notice": (
                "这是视觉模型对插图的识读，不是手册原文，可能有误。依据它的内容要写明「据组网图」"
                f"并引用 [{key}]；命令和参数仍以正文为准。"
            ),
        }

    def cache_ready(self, docset):
        """Tools have seconds, not the half-minute a first cache build takes: start it instead."""
        if docset_cache.ready(docset["revision"]):
            return True
        docset_store.dispatch_materialize(docset["id"], docset["revision"])
        return False

    def read_roots(self):
        """Real directories the DSH guard may follow links into (the doc-set cache)."""
        return [docset_cache.root()] if self.docset_catalog else []

    def authorize_path(self, name, arguments):
        """Through a link, only into doc sets opened for this question."""
        if name not in {"read", "glob", "grep"} or self.root is None:
            return
        value = arguments.get("file_path") if name == "read" else arguments.get("path") or "."
        try:
            real = resolved(self.root / str(value))
        except (OSError, ValueError, TypeError):
            raise PermissionError("Invalid path") from None
        if real.is_relative_to(self.root):
            return
        if not any(real.is_relative_to(d["path"]) for d in self.mounted.values()):
            raise PermissionError("该文档集本轮未展开")

    def docset_page(self, relative):
        """(doc set, page path) for a workspace path inside an opened doc set's pages."""
        prefix, _, rest = relative.partition("/")
        docset = self.mounted.get(prefix)
        if docset is None or not rest.startswith("pages/") or not rest.endswith(".md"):
            return None
        return docset, rest

    def workspace_path(self, value):
        """A read path as the workspace names it, whether DSH reports the link or its target."""
        path = Path(value)
        path = Path(os.path.abspath(path if path.is_absolute() else self.root / path))
        if path.is_relative_to(self.root):
            return path.relative_to(self.root).as_posix()
        real = resolved(path)
        for prefix, docset in self.mounted.items():
            if real.is_relative_to(docset["path"]):
                return prefix + "/" + real.relative_to(docset["path"]).as_posix()
        return None

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
        """Lines of a workspace file the model may cite: a section, an attachment or a page.

        None for files that only help find evidence (CATALOG.md, DOCSETS.md, a doc set's
        README, TOC and indexes).
        """
        attached = self.attachment_files.get(relative)
        if attached is not None:
            text = attached["text"]
        elif relative in self.files:
            text = self.files[relative][2].text
        elif self.docset_page(relative):
            if relative not in self.docset_lines:
                docset, page = self.docset_page(relative)
                path = docset_cache.native(docset["path"] / page)
                self.docset_lines[relative] = path.read_text(encoding="utf-8")
            text = self.docset_lines[relative]
        else:
            return None
        return text.splitlines(keepends=keepends)

    def definitions(self):
        self.check()
        # Initial diagnosis queries existing facts; fixed business operations remain in their Apps.
        definitions = registry.available_tools(
            actor_id=self.actor_id, read_only=True, evidence_only=True
        )
        self.evidence_tools = {t["name"] for t in definitions}
        if self.docset_catalog:
            definitions = [*definitions, OPEN_DEFINITION, VIEW_DEFINITION]
        return definitions

    def describe_tool(self, name, arguments):
        """Resolve a human-readable title independently from the bounded tool trace."""
        if name != "read" or not self.root:
            return ""

        try:
            relative = self.workspace_path(self.root / arguments["file_path"])
            if relative == "CATALOG.md":
                return "资料目录"
            if relative == "DOCSETS.md":
                return "产品文档集目录"
            if relative in self.attachment_files:
                return "用户附件 / " + self.attachment_files[relative]["name"]
            if relative and relative.partition("/")[0] in self.mounted:
                docset = self.mounted[relative.partition("/")[0]]
                lines = self.source_lines(relative) or []
                place = lines[0].removeprefix("> 位置：") if lines else relative.partition("/")[2]
                return (docset["title"] + " / " + place)[:200]
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
        if name == OPEN_DOCSET and self.docset_catalog:
            return self.open_docset(arguments)
        if name == VIEW_FIGURE and self.docset_catalog:
            return self.view_figure(arguments)
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
        relative = self.workspace_path(value["path"])
        if relative is None:
            raise PermissionError("Read outside document workspace")
        source = self.source_lines(relative)
        if source is None:
            return  # Catalogs, READMEs, TOCs and indexes are never evidence.
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
        markers.extend(f"[{key}]" for key in sorted(self.figure_readings))
        markers.extend(f"[{identifier}]" for identifier in sorted(self.business_evidence))
        earlier = [
            f"[{identifier}]"
            for identifier in sorted(self.earlier_evidence)
            if identifier not in self.business_evidence
        ]
        # Opened doc-set directories, so a search never starts from the root it cannot see into.
        opened = "".join(
            f"\n{prefix}：{docset['title']}" for prefix, docset in self.mounted.items()
        )
        return (
            (
                "本轮已展开的产品文档集目录（read 路径以它开头；grep、glob 的 path 设成它或其下的"
                "index、pages，从工作区根目录搜不到其中的文件）：" + opened + "\n"
                if opened
                else ""
            )
            + "服务端本轮读取记录（仅定位信息，不是原文）：\n"
            + ("\n".join(markers[:64]) if markers else "尚未读取任何可引用证据。")
            + (
                "\n本对话之前的回答取得的业务证据，也可引用（按它当时的采集时间表述）：\n"
                + "\n".join(earlier[:64])
                if earlier
                else ""
            )
            + (
                "\n插图按 [<目录>/figures/<文件>] 引用，只能引用本轮 view_figure 识读过的图，"
                "并写明内容来自组网图。"
                if self.figure_readings
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
            page = self.docset_page(filename)
            if page is not None:
                docset, path = page
                try:
                    citation = docset_store.citation(
                        docset["id"], docset["revision"], path, start, end
                    )
                except ValueError as exc:
                    raise ValueError("citation_mismatch") from exc
                # The cache must hold the stored text the citation will be checked against.
                if citation["excerpt"] != "".join(lines[start - 1 : end]):
                    raise ValueError("citation_mismatch")
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
        for prefix, figure in re.findall(DOCSET_FIGURE, answer):
            found = self.figure_readings.get(f"{prefix}/{figure}")
            if found is None:
                raise ValueError("Answer cites an unread figure")
            if found not in citations:
                citations.append(found)
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

    def malformed_references(self, answer):
        """Line references the citation format does not accept, as written."""
        return [
            candidate
            for candidate in dict.fromkeys(re.findall(r"\[[^\]\n]*:L[^\]\n]*\]", answer))
            if not re.fullmatch(CITATION, candidate)
        ]

    def unknown_evidence(self, answer):
        """Cited brackets that name no evidence of this turn or this conversation."""
        known = self.business_evidence.keys() | self.earlier_evidence.keys()
        return [
            f"[{identifier}]"
            for identifier in dict.fromkeys(re.findall(r"\[(evidence-[^\]\n]*)\]", answer))
            if identifier not in known
        ]
