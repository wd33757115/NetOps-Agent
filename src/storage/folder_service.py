# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""虚拟目录服务。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.auth.models import CurrentUser
from src.infrastructure.db.models import FileMetadata, StorageFolder
from src.infrastructure.storage.minio_client import get_minio_storage
from src.storage.folder_path import (
    ROOT_NAME,
    build_object_key,
    build_object_prefix,
    resolve_folder_chain,
    sanitize_name_segment,
)
from src.storage.permissions import check_folder_access, check_team_access
from src.storage.schemas import FolderCreateRequest, FolderResponse, FolderTreeNode

# 向后兼容：其它模块若引用 fs._sanitize_segment
_sanitize_segment = sanitize_name_segment


def _folder_to_response(row: StorageFolder) -> FolderResponse:
    return FolderResponse(
        id=row.id,
        name=row.name,
        parent_id=row.parent_id,
        visibility=row.visibility,
        team_id=row.team_id,
        owner_id=row.owner_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _rebuild_object_key(session: Session, folder: StorageFolder, filename: str, user: CurrentUser) -> str:
    del user  # object_key 仅由目录命名空间决定
    return build_object_key(session, folder, filename)


def ensure_root_folder(
    session: Session,
    *,
    user: CurrentUser,
    visibility: str,
    team_id: str | None = None,
) -> StorageFolder:
    q = session.query(StorageFolder).filter(
        StorageFolder.is_deleted.is_(False),
        StorageFolder.parent_id.is_(None),
        StorageFolder.visibility == visibility,
    )
    if visibility == "private":
        q = q.filter(StorageFolder.owner_id == user.user_id)
    else:
        if not team_id:
            raise HTTPException(status_code=400, detail="团队目录需要 team_id")
        check_team_access(session, user, team_id, write=False)
        q = q.filter(StorageFolder.team_id == team_id)

    root = q.with_for_update().first()
    if root:
        return root

    now = datetime.now(timezone.utc)
    root = StorageFolder(
        id=f"fld-{uuid.uuid4().hex[:16]}",
        name=ROOT_NAME,
        parent_id=None,
        owner_id=user.user_id if visibility == "private" else None,
        team_id=team_id if visibility == "shared" else None,
        visibility=visibility,
        path_cache="",
        created_by=user.user_id,
        created_at=now,
        updated_at=now,
        is_deleted=False,
    )
    session.add(root)
    try:
        with session.begin_nested():
            session.flush()
    except IntegrityError:
        session.expunge(root)
        dup = q.first()
        if dup:
            return dup
        raise HTTPException(status_code=500, detail="创建根目录失败")
    return root


def get_folder_or_404(session: Session, folder_id: str) -> StorageFolder:
    folder = (
        session.query(StorageFolder)
        .filter(StorageFolder.id == folder_id, StorageFolder.is_deleted.is_(False))
        .first()
    )
    if not folder:
        raise HTTPException(status_code=404, detail="目录不存在")
    return folder


def create_folder(session: Session, user: CurrentUser, req: FolderCreateRequest) -> FolderResponse:
    safe_name = sanitize_name_segment(req.name, field="文件夹名称")

    if not req.parent_id:
        parent = ensure_root_folder(
            session,
            user=user,
            visibility=req.visibility,
            team_id=req.team_id,
        )
    else:
        parent = get_folder_or_404(session, req.parent_id)
        check_folder_access(session, user, parent, write=True)
        parent_ns = resolve_folder_chain(session, parent)[1]
        if parent_ns.visibility != req.visibility:
            raise HTTPException(status_code=400, detail="子目录可见性需与父目录一致")
        if req.visibility == "shared" and req.team_id and req.team_id != parent_ns.team_id:
            raise HTTPException(status_code=400, detail="team_id 与父目录不一致")

    dup = (
        session.query(StorageFolder)
        .filter(
            StorageFolder.parent_id == parent.id,
            StorageFolder.name == safe_name,
            StorageFolder.is_deleted.is_(False),
        )
        .first()
    )
    if dup:
        raise HTTPException(status_code=409, detail="同级目录下已存在同名文件夹")

    _, parent_ns = resolve_folder_chain(session, parent)
    now = datetime.now(timezone.utc)
    folder = StorageFolder(
        id=f"fld-{uuid.uuid4().hex[:16]}",
        name=safe_name,
        parent_id=parent.id,
        owner_id=parent_ns.owner_id if parent_ns.visibility == "private" else None,
        team_id=parent_ns.team_id if parent_ns.visibility == "shared" else None,
        visibility=parent_ns.visibility,
        path_cache="",
        created_by=user.user_id,
        created_at=now,
        updated_at=now,
        is_deleted=False,
    )
    session.add(folder)
    session.flush()
    return _folder_to_response(folder)


def _collect_subtree_folder_ids(session: Session, root_id: str) -> list[str]:
    ids = [root_id]
    queue = [root_id]
    while queue:
        parent_id = queue.pop(0)
        children = (
            session.query(StorageFolder.id)
            .filter(StorageFolder.parent_id == parent_id, StorageFolder.is_deleted.is_(False))
            .all()
        )
        for (child_id,) in children:
            ids.append(child_id)
            queue.append(child_id)
    return ids


def _is_descendant(session: Session, ancestor_id: str, candidate_id: str) -> bool:
    current = get_folder_or_404(session, candidate_id)
    visited: set[str] = set()
    while current.parent_id and current.id not in visited:
        visited.add(current.id)
        if current.parent_id == ancestor_id:
            return True
        current = get_folder_or_404(session, current.parent_id)
    return False


def _relocate_files_in_folders(session: Session, user: CurrentUser, folder_ids: list[str]) -> None:
    from src.storage.object_resolver import resolve_object_key

    storage = get_minio_storage()
    files = (
        session.query(FileMetadata)
        .filter(
            FileMetadata.folder_id.in_(folder_ids),
            FileMetadata.is_deleted.is_(False),
            FileMetadata.status.in_(("active", "pending")),
        )
        .all()
    )
    for row in files:
        folder = get_folder_or_404(session, row.folder_id)
        new_key = _rebuild_object_key(session, folder, row.name, user)
        if row.object_key != new_key:
            if row.status == "active":
                source_key = resolve_object_key(session, row, repair=True)
                if source_key != new_key:
                    if not storage.copy_object(source_key, new_key):
                        raise HTTPException(status_code=500, detail=f"移动文件 {row.name} 失败")
                    storage.delete_object(source_key)
            row.object_key = new_key
        row.updated_at = datetime.now(timezone.utc)


def _purge_minio_prefixes(session: Session, folder_ids: list[str]) -> None:
    """删除子树内各目录前缀下的残留对象（含 pending 上传孤儿）。"""
    storage = get_minio_storage()
    if not storage.is_ready():
        return
    seen_prefixes: set[str] = set()
    for fid in folder_ids:
        folder = session.query(StorageFolder).filter(StorageFolder.id == fid).first()
        if not folder:
            continue
        prefix = build_object_prefix(session, folder)
        if prefix in seen_prefixes:
            continue
        seen_prefixes.add(prefix)
        storage.delete_objects_by_prefix(prefix)


def delete_folder(session: Session, user: CurrentUser, folder_id: str) -> None:
    folder = get_folder_or_404(session, folder_id)
    if folder.parent_id is None:
        raise HTTPException(status_code=400, detail="不能删除根目录")
    check_folder_access(session, user, folder, write=True)

    folder_ids = _collect_subtree_folder_ids(session, folder_id)
    storage = get_minio_storage()
    files = (
        session.query(FileMetadata)
        .filter(
            FileMetadata.folder_id.in_(folder_ids),
            FileMetadata.is_deleted.is_(False),
        )
        .all()
    )
    now = datetime.now(timezone.utc)
    for row in files:
        if row.status == "active":
            storage.delete_object(row.object_key)
        row.is_deleted = True
        row.updated_at = now

    folders = session.query(StorageFolder).filter(StorageFolder.id.in_(folder_ids)).all()
    for fld in folders:
        fld.is_deleted = True
        fld.updated_at = now

    _purge_minio_prefixes(session, folder_ids)


def rename_folder(session: Session, user: CurrentUser, folder_id: str, new_name: str) -> FolderResponse:
    folder = get_folder_or_404(session, folder_id)
    if folder.parent_id is None:
        raise HTTPException(status_code=400, detail="不能重命名根目录")
    check_folder_access(session, user, folder, write=True)

    name = sanitize_name_segment(new_name, field="文件夹名称")
    dup = (
        session.query(StorageFolder)
        .filter(
            StorageFolder.parent_id == folder.parent_id,
            StorageFolder.name == name,
            StorageFolder.is_deleted.is_(False),
            StorageFolder.id != folder.id,
        )
        .first()
    )
    if dup:
        raise HTTPException(status_code=409, detail="同级目录下已存在同名文件夹")

    folder.name = name
    folder.updated_at = datetime.now(timezone.utc)
    folder_ids = _collect_subtree_folder_ids(session, folder_id)
    _relocate_files_in_folders(session, user, folder_ids)
    session.flush()
    return _folder_to_response(folder)


def move_folder(session: Session, user: CurrentUser, folder_id: str, target_folder_id: str) -> FolderResponse:
    folder = get_folder_or_404(session, folder_id)
    if folder.parent_id is None:
        raise HTTPException(status_code=400, detail="不能移动根目录")
    target = get_folder_or_404(session, target_folder_id)
    if folder.id == target.id:
        raise HTTPException(status_code=400, detail="不能移动到自身")
    if _is_descendant(session, folder.id, target.id):
        raise HTTPException(status_code=400, detail="不能移动到子目录")

    check_folder_access(session, user, folder, write=True)
    target_ns = check_folder_access(session, user, target, write=True)
    folder_ns = resolve_folder_chain(session, folder)[1]
    if folder_ns.visibility != target_ns.visibility:
        raise HTTPException(status_code=400, detail="只能在相同空间内移动")
    if folder_ns.visibility == "shared" and folder_ns.team_id != target_ns.team_id:
        raise HTTPException(status_code=400, detail="团队目录不一致")

    dup = (
        session.query(StorageFolder)
        .filter(
            StorageFolder.parent_id == target.id,
            StorageFolder.name == folder.name,
            StorageFolder.is_deleted.is_(False),
            StorageFolder.id != folder.id,
        )
        .first()
    )
    if dup:
        raise HTTPException(status_code=409, detail="目标目录下已存在同名文件夹")

    folder.parent_id = target.id
    folder.updated_at = datetime.now(timezone.utc)
    folder_ids = _collect_subtree_folder_ids(session, folder_id)
    _relocate_files_in_folders(session, user, folder_ids)
    session.flush()
    return _folder_to_response(folder)


def list_children(
    session: Session,
    user: CurrentUser,
    *,
    folder_id: str | None,
    visibility: str,
    team_id: str | None,
) -> tuple[StorageFolder | None, list[StorageFolder]]:
    if folder_id:
        current = get_folder_or_404(session, folder_id)
        check_folder_access(session, user, current, write=False)
    else:
        current = ensure_root_folder(session, user=user, visibility=visibility, team_id=team_id)

    children = (
        session.query(StorageFolder)
        .filter(
            StorageFolder.parent_id == current.id,
            StorageFolder.is_deleted.is_(False),
        )
        .order_by(StorageFolder.name.asc())
        .all()
    )
    return current, children


def build_breadcrumb(session: Session, folder: StorageFolder | None) -> list[FolderResponse]:
    if not folder:
        return []
    chain, _ = resolve_folder_chain(session, folder)
    return [_folder_to_response(f) for f in chain]


def _load_namespace_folders(session: Session, root: StorageFolder) -> list[StorageFolder]:
    _, ns = resolve_folder_chain(session, root)
    q = session.query(StorageFolder).filter(StorageFolder.is_deleted.is_(False))
    if ns.visibility == "private":
        q = q.filter(StorageFolder.visibility == "private", StorageFolder.owner_id == ns.owner_id)
    else:
        q = q.filter(StorageFolder.visibility == "shared", StorageFolder.team_id == ns.team_id)
    return q.order_by(StorageFolder.name.asc()).all()


def build_folder_tree(session: Session, user: CurrentUser, root: StorageFolder) -> FolderTreeNode:
    check_folder_access(session, user, root, write=False)
    rows = _load_namespace_folders(session, root)
    children_map: dict[str | None, list[StorageFolder]] = {}
    for row in rows:
        children_map.setdefault(row.parent_id, []).append(row)

    def _walk(parent_id: str) -> list[FolderTreeNode]:
        return [
            FolderTreeNode(
                id=row.id,
                name=row.name,
                parent_id=row.parent_id,
                children=_walk(row.id),
            )
            for row in children_map.get(parent_id, [])
        ]

    return FolderTreeNode(
        id=root.id,
        name=root.name,
        parent_id=root.parent_id,
        children=_walk(root.id),
    )
