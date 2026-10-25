# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""解析并修复 DB object_key 与 MinIO 实际对象不一致的情况。"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from src.infrastructure.db.models import FileMetadata, StorageFolder
from src.infrastructure.storage.minio_client import get_minio_storage
from src.storage import folder_service as fs
from src.storage.folder_path import (
    build_object_key,
    build_object_prefix,
    sanitize_filename,
    sanitize_name_segment,
)


def _prefix_for_row(session: Session, row: FileMetadata, folder: StorageFolder | None) -> str:
    if folder is not None:
        return build_object_prefix(session, folder)
    if row.visibility == "shared" and row.team_id:
        return f"shared/teams/{row.team_id}"
    if row.visibility == "private" and row.owner_id:
        return f"private/users/{row.owner_id}"
    raise HTTPException(status_code=500, detail="无法确定文件命名空间")


def _basename_candidates(name: str) -> list[str]:
    out: list[str] = []
    try:
        out.append(sanitize_filename(name))
    except HTTPException:
        pass
    stripped = name.strip()
    if stripped and stripped not in out:
        out.append(stripped)
    return out


def _find_folder_by_relative_path(
    session: Session,
    root: StorageFolder,
    rel_folder_path: str,
) -> StorageFolder | None:
    if not rel_folder_path:
        return root

    current = root
    for segment in rel_folder_path.split("/"):
        if not segment:
            continue
        child = (
            session.query(StorageFolder)
            .filter(
                StorageFolder.parent_id == current.id,
                StorageFolder.is_deleted.is_(False),
                StorageFolder.name == segment,
            )
            .first()
        )
        if not child:
            try:
                safe = sanitize_name_segment(segment, field="路径段")
                child = (
                    session.query(StorageFolder)
                    .filter(
                        StorageFolder.parent_id == current.id,
                        StorageFolder.is_deleted.is_(False),
                        StorageFolder.name == safe,
                    )
                    .first()
                )
            except HTTPException:
                return None
        if not child:
            return None
        current = child
    return current


def _claim_object_key(session: Session, row: FileMetadata, actual_key: str) -> None:
    """确保 object_key 可绑定到当前记录（处理软删残留唯一约束冲突）。"""
    conflict = (
        session.query(FileMetadata)
        .filter(FileMetadata.object_key == actual_key, FileMetadata.id != row.id)
        .first()
    )
    if not conflict:
        return
    now = datetime.now(timezone.utc)
    if conflict.is_deleted:
        conflict.object_key = f"_orphan/{conflict.id}/{actual_key}"[-1024:]
        conflict.updated_at = now
        return
    raise HTTPException(status_code=409, detail="存在另一条相同存储路径的文件记录，请联系管理员处理")


def _repair_row(session: Session, row: FileMetadata, actual_key: str) -> None:
    now = datetime.now(timezone.utc)
    changed = False

    if row.object_key != actual_key:
        _claim_object_key(session, row, actual_key)
        row.object_key = actual_key
        changed = True

    folder: StorageFolder | None = None
    if row.folder_id:
        folder = session.query(StorageFolder).filter(
            StorageFolder.id == row.folder_id, StorageFolder.is_deleted.is_(False)
        ).first()

    if folder is None:
        if changed:
            row.updated_at = now
        return

    prefix = build_object_prefix(session, folder)
    normalized_prefix = prefix.rstrip("/")
    if actual_key.startswith(normalized_prefix + "/"):
        rel = actual_key[len(normalized_prefix) + 1 :]
        if "/" in rel:
            rel_dir, leaf = rel.rsplit("/", 1)
            if leaf in _basename_candidates(row.name):
                root = folder
                while root.parent_id:
                    root = fs.get_folder_or_404(session, root.parent_id)
                matched = _find_folder_by_relative_path(session, root, rel_dir)
                if matched and matched.id != row.folder_id:
                    row.folder_id = matched.id
                    changed = True

    if changed:
        row.updated_at = now


def resolve_object_key(session: Session, row: FileMetadata, *, repair: bool = True) -> str:
    """返回 MinIO 中真实存在的 object_key；必要时修复 DB 记录。"""
    storage = get_minio_storage()
    if not storage.is_ready():
        raise HTTPException(status_code=503, detail="MinIO 未就绪")

    if storage.stat_object(row.object_key):
        return row.object_key

    folder: StorageFolder | None = None
    if row.folder_id:
        folder = session.query(StorageFolder).filter(
            StorageFolder.id == row.folder_id, StorageFolder.is_deleted.is_(False)
        ).first()

    candidates: list[str] = []
    seen: set[str] = set()

    def _add(key: str | None) -> None:
        if key and key not in seen:
            seen.add(key)
            candidates.append(key)

    if folder is not None:
        try:
            _add(build_object_key(session, folder, row.name))
        except HTTPException:
            pass

    basenames = _basename_candidates(row.name)
    search_prefixes: list[str] = []
    seen_prefixes: set[str] = set()

    def _add_prefix(p: str) -> None:
        p = p.rstrip("/")
        if p and p not in seen_prefixes:
            seen_prefixes.add(p)
            search_prefixes.append(p)

    if folder is not None:
        try:
            _add_prefix(build_object_prefix(session, folder))
        except HTTPException:
            pass
    _add_prefix(_prefix_for_row(session, row, folder))
    _add_prefix(_prefix_for_row(session, row, None))

    for search_prefix in search_prefixes:
        for key in storage.list_object_keys(search_prefix, recursive=True):
            leaf = key.rsplit("/", 1)[-1]
            if leaf in basenames:
                _add(key)

    for key in candidates:
        if storage.stat_object(key):
            if repair:
                _repair_row(session, row, key)
            return key

    raise HTTPException(
        status_code=404,
        detail="文件对象在 MinIO 中不存在，可能上传未完成或已被清理，请重新上传",
    )
