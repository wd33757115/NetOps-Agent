# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""网盘权限：应用层 RBAC + 团队归属（基于目录链根命名空间）。"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from src.auth.models import CurrentUser
from src.infrastructure.db.models import FileMetadata, StorageFolder, TeamMember
from src.storage.folder_path import FolderNamespace, resolve_folder_chain


def _team_role(session: Session, team_id: str, user_id: str) -> str | None:
    member = (
        session.query(TeamMember)
        .filter(
            TeamMember.team_id == team_id,
            TeamMember.user_id == user_id,
            TeamMember.is_deleted.is_(False),
        )
        .first()
    )
    return member.role if member else None


def can_write_storage(user: CurrentUser) -> bool:
    return user.role in {"admin", "operator"}


def _check_namespace_access(session: Session, user: CurrentUser, ns: FolderNamespace, write: bool) -> None:
    if ns.visibility == "private":
        if ns.owner_id != user.user_id and not user.is_admin():
            raise HTTPException(status_code=403, detail="无权访问该个人目录")
        if write and not can_write_storage(user):
            raise HTTPException(status_code=403, detail="只读用户无法修改文件")
        return

    if ns.visibility == "shared":
        if not ns.team_id:
            raise HTTPException(status_code=403, detail="团队目录配置错误")
        role = _team_role(session, ns.team_id, user.user_id)
        if user.is_admin():
            return
        if not role:
            raise HTTPException(status_code=403, detail="非团队成员无法访问")
        if write:
            if not can_write_storage(user):
                raise HTTPException(status_code=403, detail="只读用户无法修改文件")
            if role == "viewer":
                raise HTTPException(status_code=403, detail="团队查看者无法修改文件")
        return

    raise HTTPException(status_code=403, detail="未知可见性类型")


def check_folder_access(session: Session, user: CurrentUser, folder: StorageFolder, write: bool = False) -> FolderNamespace:
    """校验目录访问权；返回已校验的根命名空间。"""
    _, ns = resolve_folder_chain(session, folder)
    _check_namespace_access(session, user, ns, write=write)
    return ns


def check_file_access(session: Session, user: CurrentUser, file_row: FileMetadata, write: bool = False) -> None:
    if file_row.is_deleted:
        raise HTTPException(status_code=404, detail="文件不存在")

    if file_row.folder_id:
        folder = session.query(StorageFolder).filter(StorageFolder.id == file_row.folder_id).first()
        if folder and not folder.is_deleted:
            ns = check_folder_access(session, user, folder, write=write)
            if file_row.visibility != ns.visibility:
                raise HTTPException(status_code=403, detail="文件元数据与目录空间不一致")
            if ns.visibility == "shared" and file_row.team_id and file_row.team_id != ns.team_id:
                raise HTTPException(status_code=403, detail="文件团队归属与目录不一致")
            if ns.visibility == "private" and file_row.owner_id and file_row.owner_id != ns.owner_id:
                raise HTTPException(status_code=403, detail="文件归属与目录不一致")
            return

    if file_row.visibility == "private":
        if file_row.owner_id != user.user_id and not user.is_admin():
            raise HTTPException(status_code=403, detail="无权访问该文件")
        if write and not can_write_storage(user):
            raise HTTPException(status_code=403, detail="只读用户无法修改文件")
        return

    if file_row.visibility == "shared" and file_row.team_id:
        role = _team_role(session, file_row.team_id, user.user_id)
        if user.is_admin():
            return
        if not role:
            raise HTTPException(status_code=403, detail="非团队成员无法访问")
        if write:
            if not can_write_storage(user):
                raise HTTPException(status_code=403, detail="只读用户无法修改文件")
            if role == "viewer":
                raise HTTPException(status_code=403, detail="团队查看者无法修改文件")
        return

    raise HTTPException(status_code=403, detail="无权访问该文件")


def check_team_access(session: Session, user: CurrentUser, team_id: str, write: bool = False) -> str | None:
    if user.is_admin():
        return "admin"
    role = _team_role(session, team_id, user.user_id)
    if not role:
        raise HTTPException(status_code=403, detail="非团队成员")
    if write and role == "viewer":
        raise HTTPException(status_code=403, detail="团队查看者无法执行写操作")
    if write and not can_write_storage(user):
        raise HTTPException(status_code=403, detail="只读用户无法执行写操作")
    return role
