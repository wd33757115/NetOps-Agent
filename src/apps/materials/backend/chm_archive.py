"""Unpack a CHM with 7-Zip into a private directory.

The listing is checked first; only pages, contents files and images are extracted.
"""

import os
import posixpath
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

PAGES = (".htm", ".html")
IMAGES = (".png", ".gif", ".jpg", ".jpeg", ".bmp")
ALLOWED = (*PAGES, ".hhc", ".hhk", *IMAGES)
MiB = 1024 * 1024
GiB = 1024 * MiB


@dataclass(frozen=True)
class Limits:
    upload_bytes: int = 4 * GiB
    entries: int = 100_000
    unpacked_bytes: int = 8 * GiB
    html_bytes: int = 16 * MiB
    image_bytes: int = 32 * MiB
    page_text_bytes: int = 2 * MiB
    text_bytes: int = 512 * MiB
    pages: int = 100_000
    toc_depth: int = 32
    seconds: int = 1800


def limits():
    """Deployment overrides, e.g. MATERIALS_DOCSET_UPLOAD_BYTES; defaults match the design."""
    values = {}
    for name, default in Limits.__dataclass_fields__.items():
        raw = os.environ.get("MATERIALS_DOCSET_" + name.upper())
        values[name] = int(raw) if raw and raw.isdigit() else default.default
    return Limits(**values)


@dataclass(frozen=True)
class Entry:
    path: str
    size: int


def executable():
    configured = os.environ.get("MATERIALS_SEVENZIP")
    candidates = (
        [configured]
        if configured
        else [
            "7zz",
            "7z",
            r"C:\Program Files\7-Zip\7z.exe",
            r"C:\Program Files (x86)\7-Zip\7z.exe",
        ]
    )
    for candidate in candidates:
        found = shutil.which(candidate) or (candidate if Path(candidate).is_file() else None)
        if found:
            return found
    raise ValueError("未找到 7-Zip：请安装 7-Zip，或用 MATERIALS_SEVENZIP 指定 7z/7zz 的路径")


def _run(arguments, seconds):
    options = {"capture_output": True, "timeout": seconds}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        options["preexec_fn"] = _bounded_memory
    try:
        result = subprocess.run([executable(), *arguments], **options)
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"解包超过 {seconds} 秒限额") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()[-300:]
        raise ValueError("7-Zip 无法读取该文件，可能不是有效的 CHM：" + (detail or "无输出"))
    return result.stdout.decode("utf-8", "replace")


def _bounded_memory():
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (4 * GiB, 4 * GiB))


def native(path):
    """Windows paths past 260 characters (alarm pages carry full OIDs) need the \\\\?\\ form."""
    path = os.path.abspath(path)
    if os.name == "nt" and not path.startswith("\\\\?\\"):
        path = "\\\\?\\" + path
    return Path(path)


def _normalize(path):
    path = path.replace("\\", "/")
    if (
        path.startswith("/")
        or re.match(r"^[A-Za-z]:", path)
        or any(part in ("..", ".") for part in path.split("/"))
        or any(ord(c) < 32 for c in path)
    ):
        raise ValueError(f"CHM 内含不安全的路径：{path[:120]}")
    return posixpath.normpath(path)


def entries(archive, seconds=60):
    """Files inside the CHM (folders and the format's own #/$ system streams excluded)."""
    output = _run(["l", "-slt", "-sccUTF-8", "--", str(archive)], seconds).replace("\r\n", "\n")
    body = output.split("\n----------\n", 1)
    if len(body) != 2:
        raise ValueError("7-Zip 没有列出 CHM 内容，可能不是有效的 CHM")
    found = []
    for block in re.split(r"\r?\n\r?\n", body[1]):
        fields = dict(line.split(" = ", 1) for line in block.splitlines() if " = " in line)
        path = fields.get("Path")
        if not path or fields.get("Folder") == "+" or "D" in fields.get("Attributes", ""):
            continue
        if path.endswith(("/", "\\")) or path.lstrip("/\\")[:1] in ("#", "$", ":"):
            continue
        size = fields.get("Size", "")
        found.append(Entry(_normalize(path), int(size) if size.isdigit() else 0))
    # The CHM handler lists folders as plain entries (no "Folder = +"): drop every parent path.
    parents = {posixpath.dirname(e.path) for e in found}
    for path in list(parents):
        while path:
            path = posixpath.dirname(path)
            parents.add(path)
    return [e for e in found if e.path not in parents]


def check(found, bounds):
    """The listing must be within limits before anything is written to disk."""
    if len(found) > bounds.entries:
        raise ValueError(f"条目数 {len(found)} 超过限额 {bounds.entries}")
    wanted = [e for e in found if e.path.lower().endswith(ALLOWED)]
    total = sum(e.size for e in wanted)
    if total > bounds.unpacked_bytes:
        raise ValueError(f"解压总量 {total} 字节超过限额 {bounds.unpacked_bytes} 字节")
    seen = {}
    for entry in wanted:
        lower = entry.path.lower()
        limit = bounds.html_bytes if lower.endswith(PAGES) else bounds.image_bytes
        if entry.size > limit and not lower.endswith((".hhc", ".hhk")):
            raise ValueError(f"文件 {entry.path} 大小 {entry.size} 字节超过限额 {limit} 字节")
        if lower in seen:
            raise ValueError(f"CHM 内有只差大小写的同名文件：{seen[lower]} 与 {entry.path}")
        seen[lower] = entry.path
    if not any(e.path.lower().endswith(PAGES) for e in wanted):
        raise ValueError("CHM 内没有 HTML 页面")
    return wanted


def extract(archive, destination, bounds):
    """Unpack the allowed types only; every file on disk must match the checked listing."""
    wanted = {e.path: e for e in check(entries(archive), bounds)}
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    includes = [f"-ir!*{suffix}" for suffix in ALLOWED]
    _run(
        ["x", "-y", "-bd", "-ssc-", f"-o{destination}", *includes, "--", str(archive)],
        bounds.seconds,
    )
    root = native(destination)
    unpacked = set()
    for current, folders, files in os.walk(root):
        for name in [*folders, *files]:
            if os.path.islink(os.path.join(current, name)):
                raise ValueError(f"CHM 解包结果含链接：{name}")
        for name in files:
            relative = Path(current, name).relative_to(root).as_posix()
            entry = wanted.get(relative)
            if entry is None or os.path.getsize(os.path.join(current, name)) != entry.size:
                raise ValueError(f"解包结果与清单不一致：{relative[:120]}")
            unpacked.add(relative)
    missing = len(wanted) - len(unpacked)
    if missing:
        raise ValueError(f"解包结果缺少 {missing} 个文件")
    return root
