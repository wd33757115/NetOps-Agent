"""Product doc sets (CHM): import jobs, publication and reading. Never calls a model.

A job goes uploading -> queued -> running -> ready -> publishing -> published; any step can
end in failed, an unpublished review expires after REVIEW, and cancel/drain stop it.
Parsing and publishing run on the package queue; requests only validate and enqueue.
"""

import difflib
import hashlib
import json
import logging
import re
import shutil
import tempfile
from collections import OrderedDict
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, insert, inspect, literal, or_, select, update
from sqlalchemy.dialects.postgresql import insert as upsert
from sqlalchemy.exc import IntegrityError

from . import chm_archive, chm_convert, library
from .documents import FILTERS, digest
from .models import DocsetFigure, DocsetFile, Document, ImportJob, PageText, Revision
from .repository import now, sessions

APP_ID = "materials"
QUEUE = "app.materials"
UPLOADS = "apps/materials/uploads/"
SOURCES = "apps/materials/sources/"
FIGURES = "apps/materials/figures/"
FIELDS = FILTERS | {"models"}
LEASE = timedelta(seconds=120)
REVIEW = timedelta(days=7)
UPLOAD_WINDOW = timedelta(hours=6)
WORKING = ("queued", "running", "publishing")
MIME = {"png": "image/png", "gif": "image/gif", "jpg": "image/jpeg", "bmp": "image/bmp"}
log = logging.getLogger(__name__)


def normalized_metadata(values):
    if set(values) - FIELDS:
        raise ValueError("Unsupported metadata")
    result = {}
    for key, value in values.items():
        value = " ".join(str(value).split())
        if len(value) > (1000 if key == "models" else 256):
            raise ValueError("Metadata value too long")
        if value:
            result[key] = value
    result["doc_type"] = "product_docset"
    return result


class UploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(min_length=5, max_length=255)
    size: int = Field(gt=0)
    document_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    expected_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("filename")
    @classmethod
    def chm_name(cls, value):
        if any(c in value for c in "/\\") or any(ord(c) < 32 for c in value):
            raise ValueError("Invalid filename")
        if not value.lower().endswith(".chm"):
            raise ValueError("Only .chm files")
        return value


class StartImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class Publication(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=256)
    metadata: dict[str, str] = Field(default_factory=dict)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    normalized_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    acknowledged_limitations: bool = False

    @field_validator("metadata")
    @classmethod
    def known_fields(cls, values):
        return normalized_metadata(values)


class StoppedError(Exception):
    """The lease moved on (cancelled, drained or reclaimed); this worker must stop quietly."""


def storage():
    from src.infrastructure.storage.minio_client import get_minio_storage

    return get_minio_storage()


def dispatch(job_id, publish=False):
    from . import tasks

    task = tasks.publish_docset if publish else tasks.import_docset
    try:
        task.apply_async(args=[job_id], queue=QUEUE)
    except Exception:
        log.warning("Doc-set job %s stays queued for reconciliation", job_id, exc_info=True)


def dispatch_materialize(document_id, revision):
    """Write the published revision to the assistant's cache ahead of the first question."""
    from . import tasks

    try:
        tasks.materialize_docset.apply_async(args=[document_id, revision], queue=QUEUE)
    except Exception:
        log.warning("Doc-set %s cache is built on first use", document_id, exc_info=True)


def describe(job, detail=False):
    summary = job.summary_json or {}
    result = {
        "id": job.id,
        "filename": job.filename,
        "size": job.size,
        "status": job.status,
        "error": job.error,
        "document_id": job.document_id,
        "expected_revision": job.expected_revision,
        "revision": job.revision,
        "source_sha256": job.source_sha256,
        "normalized_sha256": job.normalized_sha256,
        "counts": summary.get("counts"),
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "expires_at": job.expires_at,
    }
    if detail:
        result.update(
            types=summary.get("types"),
            warnings=summary.get("warnings"),
            metadata=summary.get("metadata"),
            toc=summary.get("toc"),
            publish=job.publish_json,
        )
    return result


def target_docset(session, document_id, expected_revision, lock=False):
    query = select(Document).where(Document.id == document_id)
    document = session.scalar(query.with_for_update() if lock else query)
    if document is None or document.status == "deleted" or document.kind != "docset":
        raise HTTPException(404, "文档集不存在或已删除")
    if expected_revision != document.current_revision:
        raise HTTPException(409, "文档集已经更新，请刷新后重试")
    return document


def create_upload(actor_id, body):
    bounds = chm_archive.limits()
    if body.size > bounds.upload_bytes:
        raise HTTPException(413, f"文件超过 {bounds.upload_bytes // chm_archive.GiB} GiB 上限")
    backend = storage()
    if not backend.is_ready():
        raise HTTPException(503, "对象存储未就绪")
    identity = uuid4().hex
    key = UPLOADS + identity + ".chm"
    url = backend.presigned_put_url(key, expires=3600)
    if not url:
        raise HTTPException(503, "无法生成上传地址")
    with sessions() as session:
        if body.document_id:
            target_docset(session, body.document_id, body.expected_revision)
        job = ImportJob(
            id=identity,
            actor_id=str(actor_id),
            filename=body.filename,
            size=body.size,
            upload_key=key,
            status="uploading",
            attempts=0,
            document_id=body.document_id,
            expected_revision=body.expected_revision,
            created_at=now(),
            updated_at=now(),
            expires_at=now() + UPLOAD_WINDOW,
        )
        session.add(job)
        result = describe(job)
    from src.storage.file_service import browser_upload_url

    return {"job": result, "upload_url": browser_upload_url(url), "expires_in": 3600}


def start(job_id):
    with sessions() as session:
        job = session.scalar(select(ImportJob).where(ImportJob.id == job_id).with_for_update())
        if job is None:
            raise HTTPException(404, "导入作业不存在")
        if job.status in WORKING:
            return describe(job)
        if job.status != "uploading":
            raise HTTPException(409, "导入作业已结束，请重新上传")
        stat = storage().stat_object(job.upload_key)
        if not stat:
            raise HTTPException(409, "文件尚未上传完成")
        if stat["size"] != job.size:
            raise HTTPException(409, "上传的文件大小与选择的文件不一致，请重新上传")
        job.status, job.updated_at, job.expires_at = "queued", now(), now() + REVIEW
        result = describe(job)
    dispatch(job_id)
    return result


def jobs():
    with sessions() as session:
        rows = session.scalars(
            select(ImportJob)
            .where(
                ImportJob.status.not_in(["expired", "cancelled"]),
                ImportJob.updated_at > now() - REVIEW,
            )
            .order_by(ImportJob.created_at.desc())
            .limit(50)
        )
        return [describe(row) for row in rows]


def job(job_id):
    with sessions() as session:
        row = session.get(ImportJob, job_id)
        if row is None:
            raise HTTPException(404, "导入作业不存在")
        result = describe(row, detail=True)
        if row.document_id:
            document = session.get(Document, row.document_id)
            result["target"] = document and {"id": document.id, "title": document.title}
        return result


def _manifest(session, job_id):
    row = session.get(ImportJob, job_id)
    if row is None or row.status not in {"ready", "publishing", "published"}:
        raise HTTPException(404, "导入作业没有可预览的解析结果")
    return row.manifest_json


def staged_page(job_id, path):
    with sessions() as session:
        files = {f["path"]: f for f in _manifest(session, job_id)["files"]}
        row = files.get(path)
        if row is None:
            raise HTTPException(404, "页面不存在")
        target = files[row["canonical_path"]] if row.get("canonical_path") else row
        text = session.scalar(select(PageText.text).where(PageText.sha256 == target["text_sha256"]))
        return {**_page_fields(target), "requested": path, "text": text}


def staged_titles(job_id, query):
    needle = query.strip().casefold()
    with sessions() as session:
        files = _manifest(session, job_id)["files"]
    hits = [
        _page_fields(f)
        for f in files
        if f["role"] == "page" and needle in (f.get("title") or "").casefold()
    ]
    hits.sort(key=lambda f: (f["toc_order"] is None, f["toc_order"] or 0, f["path"]))
    return {"items": hits[:50], "total": len(hits)}


def staged_figure(job_id, name):
    sha, ext = _figure_name(name)
    with sessions() as session:
        figures = _manifest(session, job_id)["figures"]
    if figures.get(sha, [None])[0] != ext:
        raise HTTPException(404, "图片不存在")
    return _figure_bytes(sha, ext)


def cancel(job_id):
    with sessions() as session:
        row = session.scalar(select(ImportJob).where(ImportJob.id == job_id).with_for_update())
        if row is None:
            raise HTTPException(404, "导入作业不存在")
        if row.status in {"published", "publishing", "cancelled", "expired"}:
            raise HTTPException(409, "该作业已无法取消")
        row.status, row.lease_token, row.lease_until, row.updated_at = (
            "cancelled",
            None,
            None,
            now(),
        )
        key, result = row.upload_key, describe(row)
    storage().delete_object(key)
    return result


def _precheck(session, document_id, expected_revision, metadata, checksum):
    """What publishing would refuse; re-run inside the publishing transaction."""
    if document_id:
        target_docset(session, document_id, expected_revision, lock=True)
    duplicate = session.scalar(
        select(Document)
        .join(
            Revision,
            (Document.id == Revision.document_id)
            & (Document.current_revision == Revision.revision),
        )
        .where(
            Revision.source_sha256 == checksum,
            Document.status != "deleted",
            Document.id != (document_id or ""),
        )
    )
    if duplicate:
        raise HTTPException(
            409, f"文件已存在：{duplicate.title}。请在现有资料上更新修订或恢复使用。"
        )
    identity = [metadata.get(k, "").casefold() for k in ("vendor", "product", "software_version")]
    if all(identity):
        rows = session.execute(
            select(Document.title, Revision.metadata_json)
            .join(
                Revision,
                (Document.id == Revision.document_id)
                & (Document.current_revision == Revision.revision),
            )
            .where(
                Document.kind == "docset",
                Document.status != "deleted",
                Document.id != (document_id or ""),
            )
        )
        for title, other in rows:
            if [
                other.get(k, "").casefold() for k in ("vendor", "product", "software_version")
            ] == identity:
                raise HTTPException(
                    409,
                    f"已有同一厂商、产品和版本的文档集：{title}。同一版本的新文档请在它上面更新修订。",
                )


def request_publish(actor_id, job_id, body):
    if not body.acknowledged_limitations:
        raise HTTPException(422, "请核对解析预览及缺失内容提示")
    with sessions() as session:
        row = session.scalar(select(ImportJob).where(ImportJob.id == job_id).with_for_update())
        if row is None:
            raise HTTPException(404, "导入作业不存在")
        if row.status == "publishing":
            return describe(row)
        if row.status != "ready":
            raise HTTPException(409, "作业不在待核对状态")
        if (body.source_sha256, body.normalized_sha256) != (
            row.source_sha256,
            row.normalized_sha256,
        ):
            raise HTTPException(409, "解析结果已变化，请重新核对")
        _precheck(session, row.document_id, row.expected_revision, body.metadata, row.source_sha256)
        row.status, row.error, row.attempts = "publishing", None, 0
        row.lease_token, row.lease_until, row.updated_at = None, None, now()
        row.publish_json = {
            "title": body.title,
            "metadata": body.metadata,
            "actor_id": str(actor_id),
        }
        result = describe(row)
    dispatch(job_id, publish=True)
    return result


def _claim(job_id, waiting, working):
    token = uuid4().hex
    stale = ImportJob.lease_until.is_(None) | (ImportJob.lease_until < now())
    eligible = (ImportJob.id == job_id) & ImportJob.status.in_([waiting, working]) & stale
    with sessions() as session:
        session.execute(
            update(ImportJob)
            .execution_options(synchronize_session=False)
            .where(eligible, ImportJob.attempts >= 3)
            .values(status="failed", error="处理多次中断，请重新导入", updated_at=now())
        )
        claimed = session.execute(
            update(ImportJob)
            .execution_options(synchronize_session=False)
            .where(eligible, ImportJob.attempts < 3)
            .values(
                status=working,
                attempts=ImportJob.attempts + 1,
                lease_token=token,
                lease_until=now() + LEASE,
                updated_at=now(),
            )
        ).rowcount
    return token if claimed == 1 else None


def _heartbeat(job_id, token, working):
    with sessions() as session:
        count = session.execute(
            update(ImportJob)
            .execution_options(synchronize_session=False)
            .where(
                ImportJob.id == job_id, ImportJob.lease_token == token, ImportJob.status == working
            )
            .values(lease_until=now() + LEASE, updated_at=now())
        ).rowcount
    if count != 1:
        raise StoppedError()


def _settle(job_id, token, working, **values):
    with sessions() as session:
        return session.execute(
            update(ImportJob)
            .execution_options(synchronize_session=False)
            .where(
                ImportJob.id == job_id, ImportJob.lease_token == token, ImportJob.status == working
            )
            .values(lease_token=None, lease_until=None, updated_at=now(), **values)
        ).rowcount


def _reason(exc):
    if isinstance(exc, HTTPException):
        return str(exc.detail)[:2000]
    if isinstance(exc, ValueError):
        return str(exc)[:2000]
    return f"处理失败（{type(exc).__name__}），请联系管理员查看 worker 日志"


def _file_digest(path):
    hasher, size = hashlib.sha256(), 0
    with open(path, "rb") as stream:
        while chunk := stream.read(1024 * 1024):
            hasher.update(chunk)
            size += len(chunk)
    return hasher.hexdigest(), size


def _store_texts(texts, beat):
    rows = [{"sha256": sha, "text": text, "created_at": now()} for sha, text in texts.items()]
    for start_at in range(0, len(rows), 1000):
        with sessions() as session:
            session.execute(
                upsert(PageText)
                .values(rows[start_at : start_at + 1000])
                .on_conflict_do_nothing(index_elements=["sha256"])
            )
        beat()


def _store_figures(root, figures, beat):
    backend = storage()
    for index, figure in enumerate(figures.values()):
        key = f"{FIGURES}{figure.sha}.{figure.ext}"
        if backend.stat_object(key) is None:
            data = (root / figure.source).read_bytes()
            if digest(data) != figure.sha or not backend.upload_file(key, data, MIME[figure.ext]):
                raise ValueError(f"图片 {figure.source} 保存失败")
        if index % 100 == 99:
            beat()


def run_import(job_id, authorize):
    token = _claim(job_id, "queued", "running")
    if token is None:
        return
    with sessions() as session:
        row = session.get(ImportJob, job_id)
        actor, key, size = row.actor_id, row.upload_key, row.size
        filename, document_id = row.filename, row.document_id

    def beat():
        authorize(actor, APP_ID)
        _heartbeat(job_id, token, "running")

    workdir = Path(tempfile.mkdtemp(prefix="chm-"))
    try:
        beat()
        archive = workdir / "source.chm"
        if not storage().download_to_file(key, archive):
            raise ValueError("上传的文件不存在或无法读取，请重新上传")
        checksum, length = _file_digest(archive)
        if length != size:
            raise ValueError("文件大小与上传时不一致，请重新上传")
        library.check_duplicate(checksum, document_id)
        beat()
        bounds = chm_archive.limits()
        root = chm_archive.extract(archive, workdir / "x", bounds)
        archive.unlink()
        beat()
        built = chm_convert.build(root, filename, bounds, beat)
        _store_texts(built.texts, beat)
        _store_figures(root, built.figures, beat)
        figures = {sha: [f.ext, f.size] for sha, f in built.figures.items()}
        _settle(
            job_id,
            token,
            "running",
            status="ready",
            error=None,
            source_sha256=checksum,
            normalized_sha256=built.normalized_sha256,
            summary_json=built.summary,
            manifest_json={"files": built.files, "figures": figures},
        )
    except StoppedError:
        return
    except HTTPException as exc:
        if exc.status_code == 503:
            return  # Authorization unavailable: the lease lapses and the reconciler retries.
        cancelled = exc.status_code in {
            401,
            403,
            404,
        }  # Only the worker authorization raises these.
        _settle(
            job_id,
            token,
            "running",
            status="cancelled" if cancelled else "failed",
            error="应用权限已撤销" if cancelled else _reason(exc),
        )
        storage().delete_object(key)
    except Exception as exc:
        if not isinstance(exc, ValueError):
            log.warning("Doc-set import %s failed", job_id, exc_info=True)
        _settle(job_id, token, "running", status="failed", error=_reason(exc))
        storage().delete_object(key)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def revision_id(title, metadata, source, files):
    return digest(
        json.dumps(
            {
                "title": title,
                "metadata": metadata,
                "source": source,
                "files": sorted(
                    [f["path"], f["role"], f["text_sha256"], f.get("canonical_path")] for f in files
                ),
            },
            sort_keys=True,
            ensure_ascii=False,
        )
    )


def readme(title, metadata, source):
    counts, types = source.get("counts", {}), source.get("types", {})
    lines = [
        f"# {title}",
        "",
        f"- 厂商：{metadata.get('vendor') or '未填写'}",
        f"- 产品：{metadata.get('product') or '未填写'}",
        f"- 软件版本：{metadata.get('software_version') or '未填写'}",
        f"- 适用型号与别名：{metadata.get('models') or '未填写'}",
        f"- 原文件：{source['filename']}（SHA-256 {source['sha256']}）",
        f"- 页面：{counts.get('unique_pages', 0)} 个；"
        f"另有 {counts.get('aliases', 0)} 个页面与已有页面内容相同",
        "- 类型：" + "，".join(f"{k} {v}" for k, v in types.items()),
        f"- 索引：命令 {counts.get('commands', 0)} 条，"
        f"告警 {counts.get('alarms', 0)} 条，日志 {counts.get('logs', 0)} 条",
        "",
        "## 缺失提示",
        "",
        *[f"- {warning}" for warning in source.get("warnings", [])],
        "",
        "## 查法",
        "",
        "命令查 index/commands.md，告警查 index/alarms.md，日志查 index/logs.md；"
        "每个索引的第一行是列名。",
        "配置任务和其他内容在 TOC.md 中按标题查找，再读取 pages/ 下的页面。",
        "每页第 1 行是该页在目录中的位置，第 2 行是类型、特性和原页面。",
        "",
    ]
    return "\n".join(lines)


def _put_text(session, text):
    sha = digest(text)
    session.execute(
        upsert(PageText)
        .values(sha256=sha, text=text, created_at=now())
        .on_conflict_do_nothing(index_elements=["sha256"])
    )
    return {
        "path": "README.md",
        "role": "readme",
        "title": "说明",
        "text_sha256": sha,
        "size": len(text.encode("utf-8")),
        "lines": text.count("\n"),
    }


FILE_COLUMNS = (
    "path",
    "role",
    "text_sha256",
    "size",
    "lines",
    "title",
    "title_path",
    "toc_order",
    "doc_type",
    "features",
    "source_path",
    "canonical_path",
    "figures",
)


def _publish(job_id, token):
    with sessions() as session:
        row = session.scalar(select(ImportJob).where(ImportJob.id == job_id).with_for_update())
        if row is None or row.lease_token != token or row.status != "publishing":
            raise StoppedError()
        publish, summary, manifest = (
            dict(row.publish_json),
            dict(row.summary_json),
            row.manifest_json,
        )
        title, metadata, actor = publish["title"], publish["metadata"], publish["actor_id"]
        _precheck(session, row.document_id, row.expected_revision, metadata, row.source_sha256)
        source = {
            "format": "chm",
            "key": f"{SOURCES}{row.source_sha256}.chm",
            "filename": row.filename,
            "sha256": row.source_sha256,
            "size": row.size,
            "normalized_sha256": row.normalized_sha256,
            "warnings": summary["warnings"],
            "counts": summary["counts"],
            "types": summary["types"],
            "publication_scope": "whole_file",
        }
        files = [*manifest["files"], _put_text(session, readme(title, metadata, source))]
        revision = revision_id(title, metadata, source, files)
        document = session.get(Document, row.document_id) if row.document_id else None
        if document is None:
            document = Document(
                id=uuid4().hex,
                title=title,
                current_revision=revision,
                created_by=actor,
                status="active",
                kind="docset",
            )
            session.add(document)
        if session.get(Revision, (document.id, revision)) is None:
            session.add(
                Revision(
                    document_id=document.id,
                    revision=revision,
                    title=title,
                    source_sha256=row.source_sha256,
                    metadata_json=metadata,
                    sections_json=[],
                    source_json=source,
                    text="",
                    created_at=now(),
                )
            )
            session.flush()
            session.execute(
                insert(DocsetFile),
                [
                    {
                        "document_id": document.id,
                        "revision": revision,
                        **{column: f.get(column) for column in FILE_COLUMNS},
                    }
                    for f in files
                ],
            )
            figures = manifest["figures"]
            if figures:
                session.execute(
                    insert(DocsetFigure),
                    [
                        {
                            "document_id": document.id,
                            "revision": revision,
                            "sha256": sha,
                            "ext": ext,
                            "size": size,
                        }
                        for sha, (ext, size) in figures.items()
                    ],
                )
        document.current_revision, document.title = revision, title
        document.source_key = row.source_sha256
        row.status, row.document_id, row.revision = "published", document.id, revision
        row.lease_token, row.lease_until, row.updated_at, row.error = None, None, now(), None
        return row.upload_key, document.id, revision


def run_publish(job_id, authorize):
    token = _claim(job_id, "publishing", "publishing")
    if token is None:
        return
    with sessions() as session:
        row = session.get(ImportJob, job_id)
        actor = (row.publish_json or {}).get("actor_id", row.actor_id)
        upload, checksum, size = row.upload_key, row.source_sha256, row.size
    try:
        authorize(actor, APP_ID)
        backend = storage()
        key = f"{SOURCES}{checksum}.chm"
        stat = backend.stat_object(key)
        if not stat or stat["size"] != size:
            if not backend.copy_object(upload, key):
                raise ValueError("原文件保存失败，请稍后重新发布")
            stat = backend.stat_object(key)
            if not stat or stat["size"] != size:
                raise ValueError("原文件保存后校验失败，请稍后重新发布")
        _heartbeat(job_id, token, "publishing")
        upload, document_id, revision = _publish(job_id, token)
    except StoppedError:
        return
    except IntegrityError:
        _settle(job_id, token, "publishing", status="ready", error="相同文件已被导入，请刷新资料库")
        return
    except HTTPException as exc:
        if exc.status_code == 503:
            return
        _settle(job_id, token, "publishing", status="ready", error=_reason(exc))
        return
    except Exception as exc:
        if not isinstance(exc, ValueError):
            log.warning("Doc-set publish %s failed", job_id, exc_info=True)
        _settle(job_id, token, "publishing", status="ready", error=_reason(exc))
        return
    storage().delete_object(upload)
    dispatch_materialize(document_id, revision)


class MetadataChange(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str = Field(min_length=1, max_length=256)
    metadata: dict[str, str] = Field(default_factory=dict)
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("metadata")
    @classmethod
    def known_fields(cls, values):
        return normalized_metadata(values)


def update_metadata(actor_id, document_id, body):
    """A new revision with the same pages: only README.md carries title and metadata."""
    with sessions() as session:
        document = target_docset(session, document_id, body.expected_revision, lock=True)
        current = session.get(Revision, (document_id, document.current_revision))
        _precheck(
            session, document_id, body.expected_revision, body.metadata, current.source_sha256
        )
        rows = (
            session.execute(
                select(
                    DocsetFile.path,
                    DocsetFile.role,
                    DocsetFile.text_sha256,
                    DocsetFile.canonical_path,
                ).where(
                    DocsetFile.document_id == document_id, DocsetFile.revision == current.revision
                )
            )
            .mappings()
            .all()
        )
        files = [dict(r) for r in rows if r["role"] != "readme"]
        files.append(_put_text(session, readme(body.title, body.metadata, current.source_json)))
        revision = revision_id(body.title, body.metadata, current.source_json, files)
        if session.get(Revision, (document_id, revision)) is None:
            session.add(
                Revision(
                    document_id=document_id,
                    revision=revision,
                    title=body.title,
                    source_sha256=current.source_sha256,
                    metadata_json=body.metadata,
                    sections_json=[],
                    source_json=current.source_json,
                    text="",
                    created_at=now(),
                )
            )
            session.flush()
            copied = [getattr(DocsetFile, c) for c in FILE_COLUMNS]
            session.execute(
                insert(DocsetFile).from_select(
                    ["document_id", "revision", *FILE_COLUMNS],
                    select(literal(document_id), literal(revision), *copied).where(
                        DocsetFile.document_id == document_id,
                        DocsetFile.revision == current.revision,
                        DocsetFile.role != "readme",
                    ),
                )
            )
            session.execute(
                insert(DocsetFile).values(document_id=document_id, revision=revision, **files[-1])
            )
            session.execute(
                insert(DocsetFigure).from_select(
                    ["document_id", "revision", "sha256", "ext", "size"],
                    select(
                        literal(document_id),
                        literal(revision),
                        DocsetFigure.sha256,
                        DocsetFigure.ext,
                        DocsetFigure.size,
                    ).where(
                        DocsetFigure.document_id == document_id,
                        DocsetFigure.revision == current.revision,
                    ),
                )
            )
        document.current_revision, document.title = revision, body.title
        result = {
            "id": document_id,
            "title": body.title,
            "revision": revision,
            "metadata": body.metadata,
            "status": document.status,
        }
    dispatch_materialize(document_id, revision)
    return result


def _revision(session, document_id, revision=None):
    document = session.get(Document, document_id)
    if (
        document is None
        or document.kind != "docset"
        or (revision is None and document.status == "deleted")
    ):
        raise HTTPException(404, "文档集不存在")
    row = session.get(Revision, (document_id, revision or document.current_revision))
    if row is None:
        raise HTTPException(404, "文档集修订不存在")
    return document, row


def _page_fields(row):
    get = row.get if isinstance(row, dict) else lambda key: getattr(row, key)
    return {
        "path": get("path"),
        "role": get("role"),
        "title": get("title"),
        "title_path": get("title_path") or [],
        "toc_order": get("toc_order"),
        "doc_type": get("doc_type"),
        "type_label": chm_convert.TYPE_LABELS.get(get("doc_type") or "", get("doc_type")),
        "features": get("features") or [],
        "source_path": get("source_path"),
        "figures": get("figures") or [],
    }


class TocTree:
    def __init__(self, text):
        self.nodes, self.children, stack = [], {-1: []}, []
        for line in text.splitlines():
            match = re.match(r"^( *)- (.*?)(?: → (pages/.+))?$", line)
            if not match:
                continue
            depth = len(match[1]) // 2
            del stack[depth:]
            parent = stack[-1] if stack else -1
            ordinal = len(self.nodes)
            self.nodes.append(
                {"id": ordinal, "title": match[2], "path": match[3], "parent": parent}
            )
            self.children.setdefault(parent, []).append(ordinal)
            stack.append(ordinal)


_trees = OrderedDict()


def _toc(session, document_id, revision):
    sha = session.scalar(
        select(DocsetFile.text_sha256).where(
            DocsetFile.document_id == document_id,
            DocsetFile.revision == revision,
            DocsetFile.path == "TOC.md",
        )
    )
    if sha not in _trees:
        text = session.scalar(select(PageText.text).where(PageText.sha256 == sha)) or ""
        _trees[sha] = TocTree(text)
        while len(_trees) > 8:
            _trees.popitem(last=False)
    _trees.move_to_end(sha)
    return _trees[sha]


def toc(document_id, revision=None, parent=-1):
    # Rows expire when the session commits; keep the revision id, not the row.
    with sessions() as session:
        current = _revision(session, document_id, revision)[1].revision
        tree = _toc(session, document_id, current)
    return {
        "revision": current,
        "items": [
            {
                **{k: tree.nodes[i][k] for k in ("id", "title", "path")},
                "children": len(tree.children.get(i, [])),
            }
            for i in tree.children.get(parent, [])
        ],
    }


def locate(document_id, path, revision=None):
    with sessions() as session:
        current = _revision(session, document_id, revision)[1].revision
        tree = _toc(session, document_id, current)
    node = next((n for n in tree.nodes if n["path"] == path), None)
    trail = []
    while node is not None:
        trail.insert(0, node["id"])
        node = tree.nodes[node["parent"]] if node["parent"] >= 0 else None
    return {"revision": current, "ids": trail}


def page(document_id, path, revision=None):
    with sessions() as session:
        _, revision_row = _revision(session, document_id, revision)
        row = session.get(DocsetFile, (document_id, revision_row.revision, path))
        if row is None:
            raise HTTPException(404, "页面不存在")
        if row.canonical_path:
            row = session.get(DocsetFile, (document_id, revision_row.revision, row.canonical_path))
        text = session.scalar(select(PageText.text).where(PageText.sha256 == row.text_sha256))
        return {
            **_page_fields(row),
            "revision": revision_row.revision,
            "requested": path,
            "text": text,
        }


def _figure_name(name):
    match = re.fullmatch(r"([0-9a-f]{64})\.(png|gif|jpg|bmp)", name)
    if not match:
        raise HTTPException(404, "图片不存在")
    return match[1], match[2]


def _figure_bytes(sha, ext):
    data = storage().download_file(f"{FIGURES}{sha}.{ext}")
    if data is None or digest(data) != sha:
        raise HTTPException(503, "图片缺失或校验失败")
    return data, MIME[ext]


def figure(document_id, name):
    sha, ext = _figure_name(name)
    with sessions() as session:
        found = session.scalar(
            select(func.count())
            .select_from(DocsetFigure)
            .where(
                DocsetFigure.document_id == document_id,
                DocsetFigure.sha256 == sha,
                DocsetFigure.ext == ext,
            )
        )
    if not found:
        raise HTTPException(404, "图片不存在")
    return _figure_bytes(sha, ext)


def _snippet(text, terms):
    """The first body line holding the whole query, else the first term, as plain text."""
    lines = [
        re.sub(r"!?\[([^\]]*)\]\((?:<[^>]*>|[^)]*)\)", r"\1", line)
        for line in text.splitlines()[2:]
        if not line.startswith("# ")  # The page title already heads each hit.
    ]
    for wanted in [" ".join(terms), terms[0]]:
        lowered = wanted.casefold()
        for line in lines:
            if lowered in line.casefold():
                clean = line.strip().lstrip("#>-|\\ ").strip()
                begin = max(0, clean.casefold().find(lowered) - 50)
                return ("…" if begin else "") + clean[begin : begin + 140]
    return ""


def search(document_id, query, revision=None, offset=0, limit=20):
    terms = [t for t in query.split() if t][:5]
    if not terms or any(len(t) > 64 for t in terms):
        raise HTTPException(422, "请输入 1 到 5 个关键词，每个不超过 64 个字符")
    patterns = ["%" + re.sub(r"([\\%_])", r"\\\1", t) + "%" for t in [*terms, " ".join(terms)]]
    phrase = patterns.pop()
    with sessions() as session:
        current = _revision(session, document_id, revision)[1].revision
        conditions = [
            DocsetFile.document_id == document_id,
            DocsetFile.revision == current,
            DocsetFile.role == "page",
            *[
                or_(DocsetFile.title.ilike(p, escape="\\"), PageText.text.ilike(p, escape="\\"))
                for p in patterns
            ],
        ]
        base = (
            select(DocsetFile)
            .join(PageText, PageText.sha256 == DocsetFile.text_sha256)
            .where(*conditions)
        )
        total = session.scalar(select(func.count()).select_from(base.subquery()))
        hits = session.execute(
            select(DocsetFile, PageText.text)
            .join(PageText, PageText.sha256 == DocsetFile.text_sha256)
            .where(*conditions)
            .order_by(
                # A command's own page first: exact title, then a title holding the phrase.
                (func.lower(DocsetFile.title) == " ".join(terms).lower()).desc(),
                DocsetFile.title.ilike(phrase, escape="\\").desc(),
                DocsetFile.title.ilike(patterns[0], escape="\\").desc(),
                DocsetFile.toc_order.asc().nulls_last(),
                DocsetFile.path,
            )
            .offset(offset)
            .limit(limit)
        ).all()
        items = [{**_page_fields(f), "snippet": _snippet(text, terms)} for f, text in hits]
    return {
        "revision": current,
        "items": items,
        "total": total,
        "next_offset": offset + limit if total > offset + limit else None,
    }


def source_link(document_id, revision):
    with sessions() as session:
        _, row = _revision(session, document_id, revision)
        source = dict(row.source_json or {})
    if source.get("format") != "chm" or source.get("key") != f"{SOURCES}{source.get('sha256')}.chm":
        raise HTTPException(404, "本修订没有原始 CHM 文件")
    backend = storage()
    stat = backend.stat_object(source["key"])
    if not stat or stat["size"] != source["size"]:
        raise HTTPException(503, "原文件缺失或校验失败")
    url = backend.presigned_download_url(source["key"], expires=300, filename=source["filename"])
    if not url:
        raise HTTPException(503, "无法生成下载地址")
    from src.storage.file_service import browser_upload_url

    return {
        "url": browser_upload_url(url),
        "expires_in": 300,
        "filename": source["filename"],
        "size": source["size"],
    }


def reconcile():
    stale = ImportJob.lease_until.is_(None) | (ImportJob.lease_until < now())
    with sessions() as session:
        waiting = session.execute(
            select(ImportJob.id, ImportJob.status)
            .where(
                ImportJob.status.in_(WORKING),
                stale,
                ImportJob.updated_at < now() - timedelta(seconds=60),
            )
            .order_by(ImportJob.created_at)
            .limit(20)
        ).all()
        expired = session.scalars(
            select(ImportJob)
            .where(ImportJob.status.in_(["uploading", "ready"]), ImportJob.expires_at < now())
            .with_for_update(skip_locked=True)
            .limit(50)
        ).all()
        keys = [job.upload_key for job in expired]
        for job in expired:
            job.status, job.updated_at = "expired", now()
    for key in keys:
        storage().delete_object(key)
    for identity, status in waiting:
        dispatch(identity, publish=status == "publishing")
    return len(waiting)


def _has_jobs_table(session):
    # Upgrading from 1.2 drains with this code before materials_0003 creates the table.
    return inspect(session.connection()).has_table(ImportJob.__tablename__)


def drain():
    with sessions() as session:
        if not _has_jobs_table(session):
            return
        keys = list(
            session.scalars(
                select(ImportJob.upload_key).where(ImportJob.status.in_(["queued", "running"]))
            )
        )
        session.execute(
            update(ImportJob)
            .where(ImportJob.status.in_(["queued", "running"]))
            .values(
                status="cancelled",
                error="应用已停用",
                lease_token=None,
                lease_until=None,
                updated_at=now(),
            )
        )
    for key in keys:
        storage().delete_object(key)


def pending():
    with sessions() as session:
        if not _has_jobs_table(session):
            return 0
        return session.scalar(
            select(func.count()).select_from(ImportJob).where(ImportJob.status.in_(WORKING))
        )


def catalog():
    """Active doc sets for the assistant: what each covers and its current revision."""
    with sessions() as session:
        rows = session.execute(
            select(Document, Revision)
            .join(
                Revision,
                (Revision.document_id == Document.id)
                & (Revision.revision == Document.current_revision),
            )
            .where(Document.kind == "docset", Document.status == "active")
            .order_by(Revision.created_at.desc())
        ).all()
        return [
            {
                "id": document.id,
                "title": document.title,
                "revision": revision.revision,
                "metadata": dict(revision.metadata_json),
                "pages": (revision.source_json or {}).get("counts", {}).get("unique_pages"),
                "created_at": revision.created_at,
            }
            for document, revision in rows
        ]


def citation(document_id, revision, path, start, end):
    """A cited line range of one page, read from the stored revision (not the cache)."""
    with sessions() as session:
        row = session.get(DocsetFile, (document_id, revision, path))
        if row is None or row.role != "page":
            raise ValueError("citation_unavailable")
        text = session.scalar(select(PageText.text).where(PageText.sha256 == row.text_sha256))
        title_path, title, original = list(row.title_path or []), row.title, row.source_path
        filename = (session.get(Revision, (document_id, revision)).source_json or {}).get(
            "filename"
        )
        docset = session.get(Document, document_id).title
    lines = text.splitlines(keepends=True)
    if not 1 <= start <= end <= len(lines):
        raise ValueError("citation_unavailable")
    excerpt = "".join(lines[start - 1 : end])
    return {
        "kind": "docset_page",
        "id": "doc-" + digest(f"{document_id}|{revision}|{path}|{start}|{end}")[:20],
        "document_id": document_id,
        "revision": revision,
        "path": path,
        "title_path": title_path or [title],
        "start_line": start,
        "end_line": end,
        "excerpt": excerpt,
        "excerpt_sha256": digest(excerpt),
        "source": {"format": "chm", "docset": docset, "filename": filename, "page": original},
    }


def validate_citation(reference):
    current = citation(
        reference["document_id"],
        reference["revision"],
        reference["path"],
        reference["start_line"],
        reference["end_line"],
    )
    if current != reference:
        raise ValueError("citation_mismatch")


MAX_DIFF_LINES = 2000


def compare(document_id, before, after, offset=0, limit=50):
    """Pages added, removed or changed between two revisions, in table-of-contents order.

    Pages align by path; a page changed when its stored text differs. Aliases of
    duplicate pages and navigation files (README, TOC, indexes) are left out.
    """
    with sessions() as session:
        old = session.get(Revision, (document_id, before))
        new = session.get(Revision, (document_id, after))
        if old is None or new is None:
            raise HTTPException(404, "待比较的修订不存在")

        def pages(revision):
            return dict(session.execute(
                select(DocsetFile.path, DocsetFile.text_sha256)
                .where(DocsetFile.document_id == document_id, DocsetFile.revision == revision,
                       DocsetFile.role == "page")
            ).all())

        older, newer = pages(before), pages(after)
        changed = [path for path in older.keys() | newer.keys()
                   if older.get(path) != newer.get(path)]
        # Titles only for what changed: reading them for every page doubles the time.
        details = {}
        for start in range(0, len(changed), 1000):
            for row in session.execute(
                select(DocsetFile.path, DocsetFile.revision, DocsetFile.title_path,
                       DocsetFile.title, DocsetFile.toc_order)
                .where(DocsetFile.document_id == document_id,
                       DocsetFile.revision.in_([before, after]),
                       DocsetFile.path.in_(changed[start : start + 1000]))
            ):
                if row.path not in details or row.revision == after:
                    details[row.path] = row
        title = {"before": old.title, "after": new.title}
        metadata = {"before": old.metadata_json, "after": new.metadata_json}
    changes = []
    for path in changed:
        row = details[path]
        changes.append({
            "kind": "added" if path not in older else "removed" if path not in newer else "changed",
            "title_path": list(row.title_path or []) or [row.title or path],
            "before_section": path if path in older else None,
            "after_section": path if path in newer else None,
            "order": (row.toc_order is None, row.toc_order or 0, path),
        })
    changes.sort(key=lambda change: change.pop("order"))
    return {
        "before": before,
        "after": after,
        "title": title,
        "metadata": metadata,
        "counts": {kind: sum(c["kind"] == kind for c in changes)
                   for kind in ("added", "removed", "changed")},
        "items": changes[offset : offset + limit],
        "next_offset": offset + limit if len(changes) > offset + limit else None,
    }


def page_diff(document_id, path, before, after):
    """Line-level difference of one page between two revisions (unified, 3 lines of context)."""

    def text(revision):
        with sessions() as session:
            row = session.get(DocsetFile, (document_id, revision, path))
            if row is None or row.role != "page":
                return None
            return session.scalar(select(PageText.text).where(PageText.sha256 == row.text_sha256))

    old, new = text(before), text(after)
    if old is None and new is None:
        raise HTTPException(404, "两个修订都没有这个页面")
    lines = []
    for line in difflib.unified_diff((old or "").splitlines(), (new or "").splitlines(),
                                     lineterm="", n=3):
        if line.startswith(("---", "+++")):
            continue
        op = line[:1] if line[:1] in "+-@" else " "
        lines.append({"op": op, "text": line[1:] if op != "@" else line})
    return {"path": path, "before": old is not None, "after": new is not None,
            "lines": lines[:MAX_DIFF_LINES], "truncated": len(lines) > MAX_DIFF_LINES}
