"""Doc-set revisions written to disk once, for the assistant's file tools.

A revision never changes, so its directory never goes stale. A question links to it
(a junction on Windows, a symlink elsewhere) instead of copying or hard-linking 20,000
files: on Windows that took 12 seconds per question.
"""

import json
import os
import shutil
import time
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from .chm_archive import native
from .models import DocsetFile, PageText
from .repository import sessions

READY = ".ready"
LIMIT = 20 * 1024**3
REPOSITORY = Path(__file__).resolve().parents[4]


def root():
    configured = os.environ.get("MATERIALS_DOCSET_CACHE")
    return Path(configured or REPOSITORY / ".runtime" / "materials-docsets").resolve()


def location(revision):
    return root() / revision


def ready(revision):
    return (location(revision) / READY).is_file()


def materialize(document_id, revision):
    """The revision's layout (aliases excluded) as files; idempotent, atomic by rename."""
    target = location(revision)
    if ready(revision):
        os.utime(target / READY)
        return target
    root().mkdir(parents=True, exist_ok=True)
    temporary = root() / f".{revision}.{uuid4().hex[:8]}"
    with sessions() as session:
        rows = session.execute(
            select(DocsetFile.path, DocsetFile.text_sha256).where(
                DocsetFile.document_id == document_id,
                DocsetFile.revision == revision,
                DocsetFile.role != "alias",
            )
        ).all()
    if not rows:
        raise ValueError("文档集修订不存在")
    total, made = 0, set()
    try:
        for start in range(0, len(rows), 500):
            batch = rows[start : start + 500]
            with sessions() as session:
                texts = dict(
                    session.execute(
                        select(PageText.sha256, PageText.text).where(
                            PageText.sha256.in_({sha for _, sha in batch})
                        )
                    ).all()
                )
            for path, sha in batch:
                file = native(temporary / path)
                if file.parent not in made:
                    file.parent.mkdir(parents=True, exist_ok=True)
                    made.add(file.parent)
                data = texts[sha].encode("utf-8")
                file.write_bytes(data)
                total += len(data)
        marker = {
            "document_id": document_id, "revision": revision, "files": len(rows), "bytes": total,
        }
        (temporary / READY).write_text(json.dumps(marker), encoding="utf-8")
        try:
            os.replace(temporary, target)
        except OSError:
            if not ready(revision):  # Another worker finished first; theirs is identical.
                raise
    finally:
        if temporary.exists():
            shutil.rmtree(native(temporary), ignore_errors=True)
    evict()
    return target


def ensure(document_id, revision):
    if ready(revision):
        os.utime(location(revision) / READY)
        return location(revision)
    return materialize(document_id, revision)


def evict(limit=LIMIT):
    """Drop the least recently used revisions past the size limit; rebuilt on next use."""
    entries = []
    for directory in root().glob("[0-9a-f]*"):
        marker = directory / READY
        if marker.is_file():
            size = json.loads(marker.read_text(encoding="utf-8")).get("bytes", 0)
            entries.append((marker.stat().st_mtime, size, directory))
    used = sum(size for _, size, _ in entries)
    for _, size, directory in sorted(entries):
        if used <= limit:
            break
        # Rename first so a reader never sees a half-deleted revision as ready.
        doomed = directory.with_name(f".gone.{directory.name}.{time.time_ns()}")
        os.replace(directory, doomed)
        shutil.rmtree(native(doomed), ignore_errors=True)
        used -= size


def link(target, at):
    """A directory link whose real path stays inside the cache (checked by the DSH guard)."""
    at = Path(at)
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(target), str(at))
    else:
        os.symlink(target, at, target_is_directory=True)
    return at


def unlink(at):
    """Remove a link without touching the cache it points to."""
    if os.name == "nt":
        os.rmdir(at)  # rmdir on a junction removes the link only.
    else:
        os.unlink(at)
