"""Files a user keeps in the netdisk, as the rest of the platform uses them.

A file is referred to as ``netdisk:<file_id>``. Reading one always goes through the
netdisk's own access check for the user doing the work, so a reference grants nothing
by itself: chat attachments, Workflow steps and App actions read as their user.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

import httpx

REFERENCE = "netdisk:"
ATTACHMENT_FOLDER = "问答附件"


def reference(file_id: str) -> str:
    return REFERENCE + file_id


def file_id_of(value) -> str | None:
    text = str(value or "").strip()
    if not text.startswith(REFERENCE):
        return None
    return text[len(REFERENCE):] or None


def _folder(session, user, name):
    """The user's private folder of that name at the netdisk root, created when missing."""
    from src.infrastructure.db.models import StorageFolder
    from src.storage import folder_service
    from src.storage.schemas import FolderCreateRequest

    root = folder_service.ensure_root_folder(session, user=user, visibility="private")
    found = session.query(StorageFolder).filter(
        StorageFolder.parent_id == root.id, StorageFolder.name == name,
        StorageFolder.is_deleted.is_(False),
    ).first()
    if found is not None:
        return found.id
    return folder_service.create_folder(
        session, user, FolderCreateRequest(name=name, parent_id=root.id, visibility="private")
    ).id


def _free_name(session, folder_id, filename):
    """A name not used in the folder: the netdisk keys objects by folder and name."""
    from src.storage.file_service import _duplicate_file_name

    path = PurePosixPath(filename)
    candidate, number = filename, 1
    while _duplicate_file_name(session, folder_id, candidate):
        number += 1
        candidate = f"{path.stem} ({number}){path.suffix}"
    return candidate


def save(user, filename: str, data: bytes, content_type: str, *, folder: str = ATTACHMENT_FOLDER) -> dict:
    """Store bytes in the user's netdisk folder, with the netdisk's own quota and permissions."""
    from src.infrastructure.db.postgres import get_db_session
    from src.infrastructure.storage.minio_client import get_minio_storage
    from src.storage import file_service
    from src.storage.schemas import UploadCompleteRequest, UploadInitRequest

    with get_db_session() as session:
        folder_id = _folder(session, user, folder)
        name = _free_name(session, folder_id, filename)
        started = file_service.init_upload(session, user, UploadInitRequest(
            filename=name, folder_id=folder_id, visibility="private",
            content_type=content_type, size_bytes=len(data),
        ))
        if not get_minio_storage().upload_file(started.object_key, data, content_type=content_type):
            raise RuntimeError("网盘存储暂不可用，文件没有保存")
        stored = file_service.complete_upload(session, user, UploadCompleteRequest(
            file_id=started.file_id, size_bytes=len(data),
        ))
        return {"file_id": stored.id, "name": stored.name, "size": stored.size_bytes,
                "content_type": stored.content_type or content_type}


def describe(user, file_id: str) -> dict:
    """Name, size and type of a file the user may read; 404 or 403 otherwise."""
    from fastapi import HTTPException

    from src.infrastructure.db.models import FileMetadata
    from src.infrastructure.db.postgres import get_db_session
    from src.storage.permissions import check_file_access

    with get_db_session() as session:
        row = session.query(FileMetadata).filter(
            FileMetadata.id == file_id, FileMetadata.is_deleted.is_(False)
        ).first()
        if row is None or row.status != "active":
            raise HTTPException(404, "网盘文件不存在")
        check_file_access(session, user, row, write=False)
        return {"file_id": row.id, "name": row.name, "size": row.size_bytes,
                "content_type": row.content_type or "application/octet-stream"}


def read(user, file_id: str) -> tuple[str, str, bytes]:
    """(name, content type, bytes) of a file the user may read."""
    from src.infrastructure.db.postgres import get_db_session
    from src.storage import file_service

    with get_db_session() as session:
        return file_service.read_file_content(session, user, file_id)


def fetch(value: str, user, *, limit: int) -> tuple[str | None, bytes]:
    """The bytes a file reference points at: a netdisk file read as ``user``, or an http(s) URL.

    Returns the file's own name when it has one (netdisk, or the URL's last path segment).
    """
    file_id = file_id_of(value)
    if file_id:
        name, _, data = read(user, file_id)
        if len(data) > limit:
            raise ValueError(f"文件超过 {limit // (1024 * 1024)} MB：{name}")
        return name, data
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError(f"只支持网盘文件或 http(s) 文件地址：{value[:200]}")
    data = bytearray()
    with httpx.stream("GET", value, timeout=60, follow_redirects=True) as response:
        if response.status_code != 200:
            raise ValueError(f"下载文件失败（HTTP {response.status_code}）：{value[:200]}")
        for chunk in response.iter_bytes():
            data.extend(chunk)
            if len(data) > limit:
                raise ValueError(f"文件超过 {limit // (1024 * 1024)} MB")
    return PurePosixPath(unquote(parts.path)).name or None, bytes(data)
