"""Turn an unpacked CHM into the doc-set layout: one Markdown file per page, TOC.md and indexes.

Local only: HTML is parsed as text, nothing is fetched, executed or sent to a model.
"""

import json
import posixpath
import re
from collections import Counter
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

from .chm_archive import IMAGES, PAGES
from .documents import digest

TYPE_LABELS = {
    "cliref": "命令参考",
    "task": "配置任务",
    "concept": "概念",
    "reference": "参考",
    "alarmref": "告警",
    "logRef": "日志",
    "topic": "主题",
    "trbCase": "故障案例",
}
NOTE_LABELS = {
    "note": "说明",
    "notice": "须知",
    "caution": "注意",
    "warning": "警告",
    "danger": "危险",
    "tip": "窍门",
}
VOID = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "wbr",
}
SKIP = {
    "script",
    "style",
    "title",
    "head",
    "noscript",
    "form",
    "iframe",
    "object",
    "embed",
    "input",
    "button",
    "select",
    "textarea",
    "map",
    "svg",
    "math",
    "template",
}
# Page chrome: parent topic, copyright and previous/next links repeat on every page.
SKIP_CLASSES = {
    "footerNavBar",
    "parentlink",
    "familylinks",
    "copyrightBottomBar",
    "copyrightBottomBar_responsive",
    "bottomNavBtn",
}
BLOCKS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "body",
    "caption",
    "center",
    "dd",
    "div",
    "dl",
    "dt",
    "figcaption",
    "figure",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "ul",
}
AUTO_CLOSE = {
    "p": {"p"},
    "li": {"li", "p"},
    "dt": {"dt", "dd", "p"},
    "dd": {"dt", "dd", "p"},
    "tr": {"tr", "td", "th", "p"},
    "td": {"td", "th", "p"},
    "th": {"td", "th", "p"},
}
# Only what would turn text into a heading or a quote (the page header uses "> ").
LINE_START = re.compile(r"^(?:#{1,6}(?:\s|$)|>)")
MAX_PATH = 150
LONG = 1800


class Node:
    __slots__ = ("tag", "attrs", "children")

    def __init__(self, tag, attrs):
        self.tag, self.attrs, self.children = tag, attrs, []

    def classes(self):
        return set((self.attrs.get("class") or "").split())


class Tree(HTMLParser):
    """Lenient tree: implied closes for p/li/td/…; text outside <pre> has its whitespace folded."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {})
        self.stack = [self.root]
        self.meta = {}

    def handle_starttag(self, tag, attrs):
        attrs = {k: v or "" for k, v in attrs}
        if tag == "meta" and attrs.get("name"):
            self.meta.setdefault(attrs["name"], []).append(attrs.get("content", ""))
        closes = AUTO_CLOSE.get(tag)
        while closes and self.stack[-1].tag in closes:
            self.stack.pop()
        if tag in BLOCKS and self.stack[-1].tag == "p":
            self.stack.pop()
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID and self.stack[-1].tag == tag:
            self.stack.pop()

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        if not any(node.tag == "pre" for node in self.stack):
            data = re.sub(r"\s+", " ", data)
        children = self.stack[-1].children
        if children and isinstance(children[-1], str):
            children[-1] += data
        else:
            children.append(data)


def decode(raw):
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig", "replace")
    match = re.search(rb"charset\s*=\s*[\"']?([A-Za-z0-9_-]+)", raw[:4096])
    charset = (match[1].decode("ascii") if match else "utf-8").lower()
    if charset in {"gb2312", "gbk", "gb18030", "x-gbk"}:
        charset = "gb18030"
    try:
        return raw.decode(charset, "replace")
    except LookupError:
        return raw.decode("utf-8", "replace")


def text_of(node):
    if isinstance(node, str):
        return node
    return "".join(text_of(child) for child in node.children)


def has_block(node):
    """An inline element (e.g. a <span>) that wraps paragraphs or lists is a container."""
    return any(
        isinstance(child, Node) and (child.tag in BLOCKS or has_block(child))
        for child in node.children
    )


def find(node, tag):
    for child in node.children:
        if isinstance(child, Node):
            if child.tag == tag:
                return child
            found = find(child, tag)
            if found is not None:
                return found
    return None


def fold(text):
    """Inline text: collapse spaces, keep <br> breaks, drop blank edges."""
    lines = [re.sub(r"[ \t\r\f\v\xa0]+", " ", line).strip() for line in text.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def escape_line(line):
    """Text that would read as Markdown structure stays literal; inline text stays greppable."""
    return "\\" + line if LINE_START.match(line) else line


def indented(text, indent):
    return "\n".join(indent + line if line else "" for line in text.split("\n"))


def wrap(line, prefix=""):
    """A line within DSH's read limit (it cuts lines at 2000 characters, and a cut line can
    never be cited): break after a sentence mark or space, continuing under the prefix."""
    pieces = []
    while len(line) > LONG:
        window = line[:LONG]
        cut = max(window.rfind(mark) + len(mark) for mark in ("。", "；", ";", "，", "、", " "))
        cut = cut if cut > LONG // 2 else LONG
        pieces.append(line[:cut].rstrip())
        line = prefix + line[cut:].lstrip()
    return [*pieces, line]


def wrapped(text, prefix=""):
    return "\n".join(piece for line in text.split("\n") for piece in wrap(line, prefix))


class Renderer:
    """One page's HTML tree to Markdown blocks; links and figures resolve through the Book."""

    def __init__(self, book, page):
        self.book = book
        self.base = posixpath.dirname(page)
        self.figures = []
        self.counts = Counter()

    def skipped(self, node):
        return node.tag in SKIP or bool(node.classes() & SKIP_CLASSES)

    def blocks(self, node, indent=""):
        out, para = [], []

        def flush():
            text = fold("".join(para))
            para.clear()
            if text:
                escaped = "\n".join(escape_line(line) for line in text.split("\n"))
                out.append(wrapped(indented(escaped, indent), indent))

        for child in node.children:
            if isinstance(child, str):
                para.append(child)
                continue
            if self.skipped(child):
                continue
            tag = child.tag
            if tag == "br":
                para.append("\n")
            elif tag == "img":
                para.append(self.image(child))
            elif tag not in BLOCKS and not has_block(child):
                para.append(self.inline(child))
            else:
                flush()
                if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                    title = fold(self.inline(child, flat=True)).replace("\n", " ")
                    if title:
                        out.append(indent + "#" * int(tag[1]) + " " + title)
                elif tag == "pre":
                    out.append(self.pre(child, indent))
                elif tag in {"ul", "ol"}:
                    out.extend(self.listing(child, indent))
                elif tag == "table":
                    out.extend(self.table(child, indent))
                elif tag == "hr":
                    continue
                elif tag == "div" and self.note_label(child):
                    out.extend(self.note(child, indent))
                else:
                    out.extend(self.blocks(child, indent))
        flush()
        return out

    def inline(self, node, flat=False):
        parts = []
        for child in node.children:
            if isinstance(child, str):
                parts.append(child)
            elif self.skipped(child):
                continue
            elif child.tag == "br":
                parts.append(" " if flat else "\n")
            elif child.tag == "img":
                parts.append(self.image(child))
            elif flat and child.tag in BLOCKS:
                parts.append(" " + self.inline(child, flat) + " ")
            else:
                parts.append(self.inline(child, flat))
        text = "".join(parts)
        if node.tag == "a" and node.attrs.get("href"):
            target = self.book.link(self.base, node.attrs["href"])
            label = fold(text).replace("\n", " ")
            if target and label:
                self.counts["links"] += 1
                return f"[{label}](<{target}>)"
        return text

    def pre(self, node, indent):
        self.counts["code_blocks"] += 1

        def raw(item):
            if isinstance(item, str):
                return item
            if item.tag == "br":
                return "\n"
            return "".join(raw(child) for child in item.children)

        text = raw(node).replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
        lines = [line.rstrip() for line in text.split("\n")]
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        longest = max([0, *(len(run) for run in re.findall(r"`+", text))])
        fence = "`" * max(3, longest + 1)
        lines = [piece for line in lines for piece in wrap(line)]
        return "\n".join(indent + line if line else "" for line in [fence, *lines, fence])

    def listing(self, node, indent):
        ordered = node.tag == "ol"
        start = node.attrs.get("start", "")
        number = int(start) - 1 if start.isdigit() else 0
        out = []
        for child in node.children:
            if not isinstance(child, Node) or self.skipped(child):
                continue
            if child.tag in {"ul", "ol"}:
                out.extend(self.listing(child, indent))
                continue
            if child.tag != "li":
                wrapper = Node("div", {})
                wrapper.children = [child]
                out.extend(self.blocks(wrapper, indent))
                continue
            number += 1
            marker = f"{number}." if ordered else "-"
            pad = indent + " " * (len(marker) + 1)
            inner = self.blocks(child, pad)
            if not inner:
                out.append(indent + marker)
            elif inner[0].startswith(pad) and not inner[0][len(pad) :].startswith(
                ("```", "|", ">")
            ):
                out.append(indent + marker + " " + inner[0][len(pad) :])
                out.extend(inner[1:])
            else:
                out.append(indent + marker)
                out.extend(inner)
        return out

    def rows(self, node):
        for child in node.children:
            if not isinstance(child, Node) or child.tag == "table":
                continue
            if child.tag == "tr":
                yield child
            else:
                yield from self.rows(child)

    def table(self, node, indent):
        self.counts["tables"] += 1
        out = []
        caption = find(node, "caption")
        if caption is not None:
            text = fold(self.inline(caption, flat=True)).replace("\n", " ")
            if text:
                out.append(indent + escape_line(text))
        grid = []
        for row in self.rows(node):
            cells = []
            for cell in row.children:
                if not isinstance(cell, Node) or cell.tag not in {"td", "th"}:
                    continue
                if find(cell, "table") is not None:
                    self.counts["nested_tables"] += 1
                text = re.sub(r"\s+", " ", self.inline(cell, flat=True)).strip()
                span = cell.attrs.get("colspan", "1")
                span = min(int(span), 64) if span.isdigit() and int(span) > 0 else 1
                if span > 1 or cell.attrs.get("rowspan", "1") not in {"", "1"}:
                    self.counts["merged_cells"] += 1
                cells.extend([text] + [""] * (span - 1))
            if cells:
                grid.append(cells)
        if not grid:
            return out
        width = max(len(cells) for cells in grid)
        grid = [cells + [""] * (width - len(cells)) for cells in grid]
        rows = ["| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |" for cells in grid]
        if all(len(indent + row) <= LONG for row in rows):
            lines = [rows[0], "|" + " --- |" * width, *rows[1:]]
            out.append("\n".join(indent + line for line in lines))
            return out
        # A table row past DSH's read limit cannot be split, so the table is listed row by
        # row: one line per cell, long cells broken like any long text.
        self.counts["wide_tables"] += 1
        header = [cell or f"第 {number} 列" for number, cell in enumerate(grid[0], 1)]
        for number, cells in enumerate(grid[1:], 1):
            out.append(indent + escape_line(f"第 {number} 行"))
            out.append("\n".join(
                wrapped(f"{indent}- {name}：{cell}", indent + "  ")
                for name, cell in zip(header, cells)
            ))
        return out

    def note_label(self, node):
        for child in node.children:
            if isinstance(child, Node) and child.tag == "img":
                name = posixpath.basename(child.attrs.get("src", "")).lower()
                key = re.split(r"[_.]", name.removeprefix("icon-"))[0]
                if key in NOTE_LABELS:
                    return NOTE_LABELS[key]
        for name in node.classes():
            if name in NOTE_LABELS:
                return NOTE_LABELS[name]
        return None

    def note(self, node, indent):
        label = self.note_label(node)
        self.counts["notes"] += 1
        body = Node("div", {})
        body.children = [
            child
            for child in node.children
            if not (
                isinstance(child, Node) and (child.tag == "img" or "notetitle" in child.classes())
            )
        ]
        inner = self.blocks(body)
        lines = []
        if inner and re.match(r"^(?:- |\d+\. |```|\|)", inner[0]):
            inner.insert(0, "")
        for index, block in enumerate(inner or [""]):
            if index:
                lines.append(">")
            text = (label + "：" + block) if index == 0 else block
            lines.extend("> " + line if line else ">" for line in text.split("\n"))
        return ["\n".join(indent + line for line in lines)]

    def image(self, node):
        source = node.attrs.get("src", "")
        if not source or "public_sys-resources/" in source.replace("\\", "/"):
            return ""
        figure = self.book.figure(self.base, source)
        if figure is None:
            self.counts["missing_figures"] += 1
            return "[图：未找到]"
        self.counts["figure_refs"] += 1
        if figure.sha not in self.figures:
            self.figures.append(figure.sha)
        alt = fold(node.attrs.get("alt") or node.attrs.get("title") or "").replace("\n", " ")
        label = ("图：" + alt) if alt else "图"
        return f"![{label.replace(']', '')}](figures/{figure.sha}.{figure.ext})"


@dataclass
class Figure:
    sha: str
    ext: str
    size: int
    source: str


@dataclass
class Page:
    source: str
    path: str
    title: str
    doc_type: str
    features: list
    body: str
    figures: list
    counts: Counter


@dataclass
class Build:
    files: list = field(default_factory=list)  # manifest rows without text
    texts: dict = field(default_factory=dict)  # sha256 -> text
    figures: dict = field(default_factory=dict)  # sha256 -> Figure
    summary: dict = field(default_factory=dict)
    normalized_sha256: str = ""


def layout_path(source):
    stem = re.sub(r"\.html?$", "", source, flags=re.IGNORECASE)
    safe = re.sub(r'[<>:"\\|?*\x00-\x1f\s]', "_", stem)
    path = "pages/" + safe + ".md"
    if len(path) > MAX_PATH:
        path = path[: MAX_PATH - 12] + "~" + digest(source)[:8] + ".md"
    return path


def sniff(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data[:6] in {b"GIF87a", b"GIF89a"}:
        return "gif"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"BM"):
        return "bmp"
    return None


class Book:
    """Everything one page needs from the others: page paths for links, figures by file."""

    def __init__(self, root, bounds):
        self.root = Path(root)
        self.bounds = bounds
        self.files = {}  # lower relative path -> relative path
        for path in sorted(self.root.rglob("*")):
            if path.is_file():
                relative = path.relative_to(self.root).as_posix()
                self.files[relative.lower()] = relative
        self.layout = {}  # lower source page -> layout path
        taken = {}
        for lower, relative in self.files.items():
            if lower.endswith(PAGES):
                path = layout_path(relative)
                if path.lower() in taken:
                    raise ValueError(f"页面 {taken[path.lower()]} 与 {relative} 转换后路径相同")
                taken[path.lower()] = relative
                self.layout[lower] = path
        self.figures = {}
        self.by_file = {}

    def resolve(self, base, reference):
        reference = unquote(reference.split("#", 1)[0].split("?", 1)[0]).replace("\\", "/")
        if not reference or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", reference):
            return None
        target = posixpath.normpath(posixpath.join(base, reference.lstrip("/")))
        return None if target.startswith("..") else target.lower()

    def link(self, base, href):
        target = self.resolve(base, href)
        return self.layout.get(target) if target else None

    def figure(self, base, source):
        target = self.resolve(base, source)
        if not target or target not in self.files or not target.endswith(IMAGES):
            return None
        if target not in self.by_file:
            data = (self.root / self.files[target]).read_bytes()
            ext = sniff(data)
            figure = None
            if ext and len(data) <= self.bounds.image_bytes:
                sha = digest(data)
                figure = self.figures.setdefault(
                    sha, Figure(sha, ext, len(data), self.files[target])
                )
            self.by_file[target] = figure
        return self.by_file[target]


class Toc(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth, self.current, self.nodes = 0, None, []

    def handle_starttag(self, tag, attrs):
        attrs = {k.lower(): v or "" for k, v in attrs}
        if tag == "ul":
            self.depth += 1
        elif tag == "object" and attrs.get("type", "").lower() == "text/sitemap":
            self.current = {"depth": max(self.depth, 1)}
        elif tag == "param" and self.current is not None:
            self.current.setdefault(attrs.get("name", "").lower(), attrs.get("value", ""))

    def handle_endtag(self, tag):
        if tag == "ul":
            self.depth = max(self.depth - 1, 0)
        elif tag == "object" and self.current is not None:
            self.nodes.append(self.current)
            self.current = None


def convert_page(book, source):
    raw = (book.root / source).read_bytes()
    text = decode(raw)
    tree = Tree()
    tree.feed(text)
    tree.close()
    body = find(tree.root, "body") or tree.root
    meta = tree.meta
    heading = find(body, "h1")
    title_tag = find(tree.root, "title")
    title = next(
        (
            fold(value).replace("\n", " ")
            for value in [
                *(meta.get("DC.Title") or [])[:1],
                text_of(title_tag) if title_tag else "",
                text_of(heading) if heading else "",
            ]
            if fold(value)
        ),
        posixpath.splitext(posixpath.basename(source))[0],
    )
    renderer = Renderer(book, source)
    blocks = renderer.blocks(body)
    if not any(block.startswith("# ") for block in blocks[:3]):
        blocks.insert(0, "# " + title)
    features = list(dict.fromkeys(v.strip() for v in meta.get("featurename", []) if v.strip()))
    return Page(
        source=source,
        path=book.layout[source.lower()],
        title=title,
        doc_type=(meta.get("DC.Type") or [""])[0].strip(),
        features=features,
        body="\n\n".join(blocks).strip() + "\n",
        figures=renderer.figures,
        counts=renderer.counts,
    ), text


def section_line(body, name):
    """First text line under a "## name" heading of a converted page."""
    match = re.search(rf"^#{{1,6}} {re.escape(name)}\s*\n+((?:[^#\n].*))$", body, re.MULTILINE)
    return match[1].strip().lstrip("\\") if match else ""


def syntax_line(body):
    """First command under "命令格式". Formats that differ by model come in groups headed
    "对于<型号>…：", which are skipped and noted."""
    match = re.search(r"^#{1,6} 命令格式[ \t]*\n((?:[^#\n].*\n?|\n)*)", body, re.MULTILINE)
    lines = [line.strip().lstrip("\\") for line in (match[1] if match else "").splitlines()]
    lines = [line for line in lines if line]
    commands = [line for line in lines if not re.fullmatch(r"对于.+[：:]", line)]
    if not commands:
        return lines[0] if lines else ""
    return commands[0] + ("（格式因型号而异，见页面）" if len(commands) < len(lines) else "")


def table_value(body, key):
    match = re.search(rf"^\| {re.escape(key)} \| ([^|\n]*?) \|", body, re.MULTILINE)
    return match[1].strip() if match else ""


def field_text(value):
    return " ".join(str(value).split()).replace("|", "\\|")


def guess_metadata(filename, samples):
    stem = re.sub(r"\.chm$", "", filename, flags=re.IGNORECASE)
    version = re.search(r"V\d{3}R\d{3}C\d{2}(?:SPC\d{3})?", stem, re.IGNORECASE)
    product = stem[: version.start()] if version else stem
    product = re.sub(r"(产品文档|\(chm\)|（chm）)", "", product, flags=re.IGNORECASE).strip(" -_")
    vendor = next(
        (
            name
            for name, marks in (
                ("华为", ("华为技术有限公司", "Huawei Technologies")),
                ("H3C", ("新华三", "H3C")),
            )
            if any(mark in text for text in samples for mark in marks)
        ),
        "",
    )
    series = re.match(r"^([A-Za-z][A-Za-z-]*)\s+([0-9][0-9A-Za-z,，、\s]*)", product)
    models = []
    if series:
        models = [f"{series[1]} {n}" for n in re.findall(r"\d{3,5}[A-Z]*", series[2])]
    return {
        "vendor": vendor,
        "product": product,
        "software_version": version[0].upper() if version else "",
        "doc_type": "product_docset",
        "models": ", ".join(models),
    }


def build(root, filename, bounds, checkpoint=lambda: None):
    """The whole layout except README.md: it carries title and metadata, written at publish."""
    book = Book(root, bounds)
    sources = [book.files[lower] for lower in book.layout]
    if len(sources) > bounds.pages:
        raise ValueError(f"页面数 {len(sources)} 超过限额 {bounds.pages}")
    contents = [book.files[k] for k in book.files if k.endswith(".hhc")]
    toc = Toc()
    if contents:
        largest = max(contents, key=lambda name: (book.root / name).stat().st_size)
        toc.feed(decode((book.root / largest).read_bytes()))
        toc.close()
    depth = max([0, *(node["depth"] for node in toc.nodes)])
    if depth > bounds.toc_depth:
        raise ValueError(f"目录深度 {depth} 超过限额 {bounds.toc_depth}")

    pages, counts, failed, samples = {}, Counter(), [], []
    for index, source in enumerate(sources):
        if index % 500 == 0:
            checkpoint()
        try:
            page, html = convert_page(book, source)
        except (RecursionError, ValueError, OSError) as exc:
            failed.append(f"{source}（{type(exc).__name__}）")
            continue
        if len(samples) < 20:
            samples.append(html)
        if len(page.body.encode("utf-8")) > bounds.page_text_bytes:
            raise ValueError(f"页面 {source} 正文超过 {bounds.page_text_bytes} 字节限额")
        counts.update(page.counts)
        pages[source.lower()] = page

    # A page's place in the TOC: its first node gives the title path and the reading order.
    crumbs, order, nodes, trail = {}, {}, [], []
    for ordinal, node in enumerate(toc.nodes):
        trail = trail[: node["depth"] - 1] + [
            fold(node.get("name", "")).replace("\n", " ") or "（无标题）"
        ]
        local = book.resolve("", node.get("local", "")) if node.get("local") else None
        if local and local not in pages:
            counts["toc_missing"] += 1
            local = None
        if local and local not in crumbs:
            crumbs[local], order[local] = list(trail), ordinal
        nodes.append((node["depth"], trail[-1], local))

    # Identical pages (same body) are kept once; TOC lines, indexes and links use that copy.
    canonical, first = {}, {}
    for lower in sorted(pages, key=lambda key: (order.get(key, len(toc.nodes)), key)):
        key = digest(pages[lower].body)
        first.setdefault(key, lower)
        if first[key] != lower:
            canonical[lower] = first[key]
    aliases = {pages[a].path: pages[c].path for a, c in canonical.items()}

    def relink(body):
        return re.sub(
            r"\]\(<(pages/[^>]+)>\)", lambda m: "](<" + aliases.get(m[1], m[1]) + ">)", body
        )

    result = Build()
    total = 0
    for lower, page in pages.items():
        crumb = crumbs.get(lower)
        row = {
            "path": page.path,
            "role": "page",
            "title": page.title[:512],
            "title_path": crumb or [],
            "toc_order": order.get(lower),
            "doc_type": page.doc_type[:32],
            "features": page.features,
            "source_path": page.source,
            "canonical_path": None,
            "figures": page.figures,
        }
        if lower in canonical:
            target = pages[canonical[lower]]
            row.update(role="alias", canonical_path=target.path, figures=[])
            text = None
        else:
            kind = TYPE_LABELS.get(page.doc_type, page.doc_type or "页面")
            details = [f"类型：{kind}"]
            if page.features:
                details.append("特性：" + "，".join(page.features))
            details.append("原页面：" + page.source)
            text = (
                "> 位置："
                + (" / ".join(crumb) if crumb else "（不在目录中）")
                + "\n"
                + "> "
                + " | ".join(details)
                + "\n\n"
                + relink(page.body)
            )
        result.files.append((row, text))
        if text is not None:
            total += len(text.encode("utf-8"))
    if total > bounds.text_bytes:
        raise ValueError(f"正文总量 {total} 字节超过限额 {bounds.text_bytes} 字节")

    toc_lines = []
    for depth_level, name, local in nodes:
        name = name.replace("→", "->")
        target = pages[canonical.get(local, local)].path if local else None
        toc_lines.append(
            "  " * (depth_level - 1) + f"- {name}" + (f" → {target}" if target else "")
        )
    result.files.append(
        ({"path": "TOC.md", "role": "toc", "title": "目录"}, "\n".join(toc_lines) + "\n")
    )

    indexes = {"commands": [], "alarms": [], "logs": []}
    for lower, page in pages.items():
        if lower in canonical:
            continue
        crumb = " / ".join(crumbs.get(lower) or [])
        if page.doc_type == "cliref":
            syntax = syntax_line(page.body)
            if not syntax:
                continue  # A chapter page such as "AAA配置命令", not a command.
            command = re.sub(r"[（(][^（()）]*[)）]\s*$", "", page.title).strip() or page.title
            indexes["commands"].append(
                [
                    command,
                    section_line(page.body, "视图"),
                    ",".join(page.features),
                    syntax,
                    crumb,
                    page.path,
                ]
            )
        elif page.doc_type == "alarmref":
            match = re.match(r"^(\S+?)_((?:\d+\.){4,}\d+)\s+(\S.*)$", page.title)
            if match:
                indexes["alarms"].append(
                    [
                        match[3],
                        match[2],
                        match[1],
                        table_value(page.body, "Alarm or Event"),
                        table_value(page.body, "Trap Severity"),
                        page.path,
                    ]
                )
        elif page.doc_type == "logRef":
            match = re.match(r"^([^/\s]+)/(\d)/(\S+)", page.title)
            if match:
                indexes["logs"].append([page.title, match[1], match[2], page.path])
    headers = {
        "commands": "命令 | 视图 | 特性 | 命令格式首行 | 目录位置 | 文件",
        "alarms": "告警名 | OID | 模块 | 类型 | 级别 | 文件",
        "logs": "日志 ID | 模块 | 级别 | 文件",
    }
    for name, rows in indexes.items():
        lines = [" | ".join(field_text(value) for value in row) for row in sorted(rows)]
        title = {"commands": "命令索引", "alarms": "告警索引", "logs": "日志索引"}[name]
        result.files.append(
            (
                {"path": f"index/{name}.md", "role": "index", "title": title},
                headers[name] + "\n" + "\n".join(lines) + ("\n" if lines else ""),
            )
        )

    manifest, shas = [], {}
    for row, text in result.files:
        if text is not None:
            sha = shas[row["path"]] = digest(text)
            result.texts[sha] = text
            row.update(text_sha256=sha, size=len(text.encode("utf-8")), lines=text.count("\n"))
    for row, text in result.files:
        if text is None:
            row.update(text_sha256=shas[row["canonical_path"]], size=0, lines=0)
        manifest.append(row)
    result.files = sorted(manifest, key=lambda row: row["path"])
    result.figures = {
        sha: book.figures[sha] for row in manifest for sha in row.get("figures") or []
    }
    result.normalized_sha256 = digest(json.dumps(result.files, ensure_ascii=False, sort_keys=True))

    types = Counter(p.doc_type or "（未标注）" for k, p in pages.items() if k not in canonical)
    counts.update(
        pages=len(pages),
        unique_pages=len(pages) - len(canonical),
        aliases=len(canonical),
        toc_nodes=len(toc.nodes),
        not_in_toc=sum(1 for lower in pages if lower not in crumbs),
        failed_pages=len(failed),
        figures=len(result.figures),
        commands=len(indexes["commands"]),
        alarms=len(indexes["alarms"]),
        logs=len(indexes["logs"]),
        text_bytes=total,
    )
    counts["toc_depth"] = depth
    warnings = ["按页面文字结构提取，保留目录、表格和代码块；不还原页面版式。"]
    for key, message in (
        ("figures", "图片仅保存原图供查看，图中文字未提取"),
        ("missing_figures", "页面引用的图片在 CHM 中不存在"),
        ("not_in_toc", "页面不在目录中，位置显示为「不在目录中」"),
        ("toc_missing", "目录条目指向的页面不存在"),
        ("aliases", "页面与其他页面内容完全相同，只保留一份"),
        ("merged_cells", "表格含合并单元格，按行列展开，含义需核对原页面"),
        ("nested_tables", "嵌套表格按单元格文字读取，内层布局未还原"),
        ("wide_tables", "表格行过长，改为逐行逐项列出"),
    ):
        if counts[key]:
            warnings.append(f"{message}（{counts[key]} 处）")
    if failed:
        warnings.append(f"{len(failed)} 个页面转换失败，未发布：" + "、".join(failed[:5]))
    if not toc.nodes:
        warnings.append("CHM 内没有目录文件（.hhc），页面按路径排列")
    result.summary = {
        "counts": dict(counts),
        "types": {TYPE_LABELS.get(k, k): v for k, v in types.most_common()},
        "warnings": warnings,
        "metadata": guess_metadata(filename, samples),
        "toc": [
            {
                "depth": d,
                "title": name,
                "path": pages[canonical.get(local, local)].path if local else None,
            }
            for d, name, local in nodes
            if d <= 3
        ][:2000],
    }
    return result
