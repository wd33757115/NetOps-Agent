# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""文件上传/下载/分享服务。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from src.auth.models import CurrentUser
from src.common.config import get_settings
from src.infrastructure.db.models import FileMetadata, StorageFolder, Team, TeamMember
from src.infrastructure.storage.minio_client import get_minio_storage
from src.storage import folder_service as fs
from src.storage.folder_path import assert_key_within_prefix, build_object_key, build_object_prefix
from src.storage.object_resolver import resolve_object_key
from src.storage.permissions import check_file_access, check_folder_access, check_team_access, can_write_storage
from src.storage.schemas import (
    DownloadResponse,
    FileResponse,
    MoveRequest,
    RenameRequest,
    ShareFileRequest,
    ShareFolderRequest,
    TeamCreateRequest,
    TeamMemberAddRequest,
    TeamMemberResponse,
    TeamResponse,
    UploadCompleteRequest,
    UploadInitRequest,
    UploadInitResponse,
)

UPLOAD_EXPIRES = 3600
DOWNLOAD_EXPIRES = 3600 * 24
# Browsers upload through the workbench's own address: the frontend server forwards
# /object-storage/... to MinIO unchanged, so uploads work from any machine that can open
# the workbench, while MinIO itself stays on an internal address (127.0.0.1, minio:9000).
UPLOAD_PROXY_PREFIX = "/object-storage"


def browser_upload_url(presigned_url: str) -> str:
    """The presigned PUT as a path on the workbench origin; its signature still names MinIO's host."""
    parts = urlsplit(presigned_url)
    return f"{UPLOAD_PROXY_PREFIX}{parts.path}?{parts.query}"
settings = get_settings()


def _file_to_response(row: FileMetadata) -> FileResponse:
    return FileResponse(
        id=row.id,
        name=row.name,
        folder_id=row.folder_id,
        visibility=row.visibility,
        team_id=row.team_id,
        owner_id=row.owner_id,
        content_type=row.content_type,
        size_bytes=row.size_bytes or 0,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def list_files_in_folder(session: Session, folder_id: str) -> list[FileMetadata]:
    return (
        session.query(FileMetadata)
        .filter(
            FileMetadata.folder_id == folder_id,
            FileMetadata.is_deleted.is_(False),
            FileMetadata.status == "active",
        )
        .order_by(FileMetadata.name.asc())
        .all()
    )


def _check_user_quota(session: Session, owner_id: str, additional_bytes: int) -> None:
    if additional_bytes <= 0:
        return
    used = (
        session.query(func.coalesce(func.sum(FileMetadata.size_bytes), 0))
        .filter(
            FileMetadata.owner_id == owner_id,
            FileMetadata.visibility == "private",
            FileMetadata.is_deleted.is_(False),
            FileMetadata.status == "active",
        )
        .scalar()
    )
    if int(used or 0) + additional_bytes > settings.STORAGE_MAX_USER_BYTES:
        raise HTTPException(status_code=413, detail="个人存储空间已达上限")


def init_upload(session: Session, user: CurrentUser, req: UploadInitRequest) -> UploadInitResponse:
    if not can_write_storage(user):
        raise HTTPException(status_code=403, detail="只读用户无法上传")

    if req.size_bytes > settings.STORAGE_MAX_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"单文件大小不能超过 {settings.STORAGE_MAX_FILE_BYTES // (1024 * 1024)}MB",
        )

    if req.folder_id:
        folder = fs.get_folder_or_404(session, req.folder_id)
    else:
        folder = fs.ensure_root_folder(
            session,
            user=user,
            visibility=req.visibility,
            team_id=req.team_id,
        )

    ns = check_folder_access(session, user, folder, write=True)
    if req.visibility != ns.visibility:
        raise HTTPException(status_code=400, detail="visibility 与目标目录不一致")
    if ns.visibility == "shared":
        if req.team_id and req.team_id != ns.team_id:
            raise HTTPException(status_code=400, detail="team_id 与目标目录不一致")
        check_team_access(session, user, ns.team_id or "", write=True)
    elif ns.owner_id:
        _check_user_quota(session, ns.owner_id, req.size_bytes)

    object_key = build_object_key(session, folder, req.filename)
    prefix = build_object_prefix(session, folder)
    assert_key_within_prefix(object_key, prefix)

    file_id = f"file-{uuid.uuid4().hex[:16]}"
    now = datetime.now(timezone.utc)

    row = FileMetadata(
        id=file_id,
        name=req.filename.strip(),
        folder_id=folder.id,
        object_key=object_key,
        owner_id=ns.owner_id if ns.visibility == "private" else None,
        team_id=ns.team_id if ns.visibility == "shared" else None,
        visibility=ns.visibility,
        content_type=req.content_type,
        size_bytes=req.size_bytes,
        status="pending",
        created_by=user.user_id,
        created_at=now,
        updated_at=now,
        is_deleted=False,
    )
    session.add(row)
    session.flush()

    storage = get_minio_storage()
    if not storage.is_ready():
        raise HTTPException(status_code=503, detail="MinIO 未就绪")
    upload_url = storage.presigned_put_url(object_key, expires=UPLOAD_EXPIRES)
    if not upload_url:
        raise HTTPException(status_code=500, detail="无法生成上传地址")

    return UploadInitResponse(
        file_id=file_id,
        object_key=object_key,
        upload_url=browser_upload_url(upload_url),
        expires_in=UPLOAD_EXPIRES,
    )


def complete_upload(session: Session, user: CurrentUser, req: UploadCompleteRequest) -> FileResponse:
    row = session.query(FileMetadata).filter(FileMetadata.id == req.file_id).first()
    if not row or row.is_deleted:
        raise HTTPException(status_code=404, detail="文件记录不存在")
    check_file_access(session, user, row, write=True)

    if row.folder_id:
        folder = fs.get_folder_or_404(session, row.folder_id)
        assert_key_within_prefix(row.object_key, build_object_prefix(session, folder))

    storage = get_minio_storage()
    object_key = resolve_object_key(session, row, repair=True)
    stat = storage.stat_object(object_key)
    if not stat:
        raise HTTPException(status_code=400, detail="对象尚未上传至 MinIO，请先完成 PUT 上传")

    row.status = "active"
    row.size_bytes = req.size_bytes if req.size_bytes is not None else stat.get("size", 0)
    row.etag = stat.get("etag")
    row.content_type = row.content_type or stat.get("content_type")
    row.updated_at = datetime.now(timezone.utc)
    session.flush()
    return _file_to_response(row)


def get_download_url(session: Session, user: CurrentUser, file_id: str) -> DownloadResponse:
    row = session.query(FileMetadata).filter(FileMetadata.id == file_id, FileMetadata.is_deleted.is_(False)).first()
    if not row or row.status != "active":
        raise HTTPException(status_code=404, detail="文件不存在")
    check_file_access(session, user, row, write=False)

    object_key = resolve_object_key(session, row, repair=True)
    storage = get_minio_storage()
    url = storage.get_presigned_url(object_key, expires=DOWNLOAD_EXPIRES)
    if not url:
        raise HTTPException(status_code=500, detail="无法生成下载地址")
    return DownloadResponse(
        file_id=row.id,
        filename=row.name,
        download_url=url,
        expires_in=DOWNLOAD_EXPIRES,
    )


def read_file_content(
    session: Session,
    user: CurrentUser,
    file_id: str,
) -> tuple[str, str, bytes]:
    """返回 (filename, content_type, data)。"""
    row = session.query(FileMetadata).filter(FileMetadata.id == file_id, FileMetadata.is_deleted.is_(False)).first()
    if not row or row.status != "active":
        raise HTTPException(status_code=404, detail="文件不存在")
    check_file_access(session, user, row, write=False)

    object_key = resolve_object_key(session, row, repair=True)
    storage = get_minio_storage()
    if not storage.is_ready():
        raise HTTPException(status_code=503, detail="MinIO 未就绪")

    data = storage.download_file(object_key)
    if data is None:
        raise HTTPException(status_code=404, detail="文件对象在 MinIO 中不存在，请重新上传")

    content_type = row.content_type or "application/octet-stream"
    return row.name, content_type, data


def delete_file(session: Session, user: CurrentUser, file_id: str) -> None:
    row = session.query(FileMetadata).filter(FileMetadata.id == file_id, FileMetadata.is_deleted.is_(False)).first()
    if not row:
        raise HTTPException(status_code=404, detail="文件不存在")
    check_file_access(session, user, row, write=True)

    object_key = resolve_object_key(session, row, repair=False)
    storage = get_minio_storage()
    storage.delete_object(object_key)
    row.is_deleted = True
    row.updated_at = datetime.now(timezone.utc)


def _duplicate_file_name(session: Session, folder_id: str, name: str, exclude_id: str | None = None) -> bool:
    q = session.query(FileMetadata).filter(
        FileMetadata.folder_id == folder_id,
        FileMetadata.name == name,
        FileMetadata.is_deleted.is_(False),
        FileMetadata.status == "active",
    )
    if exclude_id:
        q = q.filter(FileMetadata.id != exclude_id)
    return q.first() is not None


def rename_file(session: Session, user: CurrentUser, file_id: str, req: RenameRequest) -> FileResponse:
    row = session.query(FileMetadata).filter(FileMetadata.id == file_id, FileMetadata.is_deleted.is_(False)).first()
    if not row or row.status != "active":
        raise HTTPException(status_code=404, detail="文件不存在")
    check_file_access(session, user, row, write=True)

    name = req.name.strip()
    if _duplicate_file_name(session, row.folder_id, name, exclude_id=row.id):
        raise HTTPException(status_code=409, detail="同级目录下已存在同名文件")

    folder = fs.get_folder_or_404(session, row.folder_id)
    object_key = resolve_object_key(session, row, repair=True)
    new_key = build_object_key(session, folder, name)
    storage = get_minio_storage()
    if object_key != new_key:
        if not storage.copy_object(object_key, new_key):
            raise HTTPException(status_code=500, detail="重命名文件失败")
        storage.delete_object(object_key)
        row.object_key = new_key

    row.name = name
    row.updated_at = datetime.now(timezone.utc)
    session.flush()
    return _file_to_response(row)


def _suggest_copy_name(session: Session, folder_id: str, source_name: str, source_folder_id: str | None) -> str:
    """生成复制文件名：同目录或重名时追加「副本」后缀。"""
    if folder_id != source_folder_id and not _duplicate_file_name(session, folder_id, source_name):
        return source_name

    base, dot, ext = source_name.rpartition(".")
    if not dot:
        base, ext = source_name, ""
    ext_part = f".{ext}" if ext else ""
    candidate = f"{base} - 副本{ext_part}"
    n = 2
    while _duplicate_file_name(session, folder_id, candidate):
        candidate = f"{base} - 副本 ({n}){ext_part}"
        n += 1
    return candidate


def copy_file(session: Session, user: CurrentUser, file_id: str, req: CopyFileRequest) -> FileResponse:
    source = session.query(FileMetadata).filter(FileMetadata.id == file_id, FileMetadata.is_deleted.is_(False)).first()
    if not source or source.status != "active":
        raise HTTPException(status_code=404, detail="源文件不存在")
    check_file_access(session, user, source, write=False)

    target = fs.get_folder_or_404(session, req.target_folder_id)
    check_folder_access(session, user, target, write=True)
    if source.visibility != target.visibility:
        raise HTTPException(status_code=400, detail="只能在相同空间内复制")
    if source.visibility == "shared" and source.team_id != target.team_id:
        raise HTTPException(status_code=400, detail="团队目录不一致")

    name = (req.name or "").strip() or _suggest_copy_name(session, target.id, source.name, source.folder_id)
    if _duplicate_file_name(session, target.id, name):
        raise HTTPException(status_code=409, detail="目标目录下已存在同名文件")

    dest_key = build_object_key(session, target, name)
    storage = get_minio_storage()
    source_key = resolve_object_key(session, source, repair=True)
    if not storage.copy_object(source_key, dest_key):
        raise HTTPException(status_code=500, detail="复制文件失败")

    now = datetime.now(timezone.utc)
    new_row = FileMetadata(
        id=f"file-{uuid.uuid4().hex[:16]}",
        name=name,
        folder_id=target.id,
        object_key=dest_key,
        owner_id=source.owner_id if target.visibility == "private" else user.user_id,
        team_id=target.team_id if target.visibility == "shared" else None,
        visibility=target.visibility,
        content_type=source.content_type,
        size_bytes=source.size_bytes,
        etag=source.etag,
        status="active",
        created_by=user.user_id,
        created_at=now,
        updated_at=now,
        is_deleted=False,
    )
    session.add(new_row)
    session.flush()
    return _file_to_response(new_row)


def move_file(session: Session, user: CurrentUser, file_id: str, req: MoveRequest) -> FileResponse:
    row = session.query(FileMetadata).filter(FileMetadata.id == file_id, FileMetadata.is_deleted.is_(False)).first()
    if not row or row.status != "active":
        raise HTTPException(status_code=404, detail="文件不存在")
    check_file_access(session, user, row, write=True)

    target = fs.get_folder_or_404(session, req.target_folder_id)
    check_folder_access(session, user, target, write=True)
    if row.visibility != target.visibility:
        raise HTTPException(status_code=400, detail="只能在相同空间内移动")
    if row.visibility == "shared" and row.team_id != target.team_id:
        raise HTTPException(status_code=400, detail="团队目录不一致")
    if _duplicate_file_name(session, target.id, row.name, exclude_id=row.id):
        raise HTTPException(status_code=409, detail="目标目录下已存在同名文件")

    object_key = resolve_object_key(session, row, repair=True)
    new_key = build_object_key(session, target, row.name)
    storage = get_minio_storage()
    if object_key != new_key:
        if not storage.copy_object(object_key, new_key):
            raise HTTPException(status_code=500, detail="移动文件失败")
        storage.delete_object(object_key)
        row.object_key = new_key

    row.folder_id = target.id
    row.team_id = target.team_id if target.visibility == "shared" else None
    row.updated_at = datetime.now(timezone.utc)
    session.flush()
    return _file_to_response(row)


def share_file_to_team(session: Session, user: CurrentUser, req: ShareFileRequest) -> FileResponse:
    source = session.query(FileMetadata).filter(FileMetadata.id == req.file_id, FileMetadata.is_deleted.is_(False)).first()
    if not source or source.status != "active":
        raise HTTPException(status_code=404, detail="源文件不存在")
    check_file_access(session, user, source, write=True)
    check_team_access(session, user, req.team_id, write=True)

    if req.target_folder_id:
        target_folder = fs.get_folder_or_404(session, req.target_folder_id)
        if target_folder.visibility != "shared" or target_folder.team_id != req.team_id:
            raise HTTPException(status_code=400, detail="目标目录须为同一团队的共享目录")
    else:
        target_folder = fs.ensure_root_folder(session, user=user, visibility="shared", team_id=req.team_id)

    dest_key = build_object_key(session, target_folder, source.name)

    storage = get_minio_storage()
    source_key = resolve_object_key(session, source, repair=True)
    if not storage.copy_object(source_key, dest_key):
        raise HTTPException(status_code=500, detail="复制到团队空间失败")

    now = datetime.now(timezone.utc)
    new_row = FileMetadata(
        id=f"file-{uuid.uuid4().hex[:16]}",
        name=source.name,
        folder_id=target_folder.id,
        object_key=dest_key,
        owner_id=user.user_id,
        team_id=req.team_id,
        visibility="shared",
        content_type=source.content_type,
        size_bytes=source.size_bytes,
        etag=source.etag,
        status="active",
        created_by=user.user_id,
        created_at=now,
        updated_at=now,
        is_deleted=False,
    )
    session.add(new_row)
    session.flush()
    return _file_to_response(new_row)


def _copy_file_to_team_folder(
    session: Session,
    user: CurrentUser,
    source: FileMetadata,
    target_folder: StorageFolder,
    team_id: str,
) -> FileResponse:
    dest_key = build_object_key(session, target_folder, source.name)
    if (
        session.query(FileMetadata)
        .filter(
            FileMetadata.folder_id == target_folder.id,
            FileMetadata.name == source.name,
            FileMetadata.is_deleted.is_(False),
            FileMetadata.status == "active",
        )
        .first()
    ):
        raise HTTPException(status_code=409, detail=f"目标目录已存在文件 {source.name}")

    storage = get_minio_storage()
    source_key = resolve_object_key(session, source, repair=True)
    if not storage.copy_object(source_key, dest_key):
        raise HTTPException(status_code=500, detail=f"复制文件 {source.name} 失败")

    now = datetime.now(timezone.utc)
    new_row = FileMetadata(
        id=f"file-{uuid.uuid4().hex[:16]}",
        name=source.name,
        folder_id=target_folder.id,
        object_key=dest_key,
        owner_id=user.user_id,
        team_id=team_id,
        visibility="shared",
        content_type=source.content_type,
        size_bytes=source.size_bytes,
        etag=source.etag,
        status="active",
        created_by=user.user_id,
        created_at=now,
        updated_at=now,
        is_deleted=False,
    )
    session.add(new_row)
    session.flush()
    return _file_to_response(new_row)


def _ensure_shared_subfolder(
    session: Session,
    user: CurrentUser,
    team_id: str,
    parent_id: str,
    name: str,
) -> StorageFolder:
    existing = (
        session.query(StorageFolder)
        .filter(
            StorageFolder.parent_id == parent_id,
            StorageFolder.name == name,
            StorageFolder.is_deleted.is_(False),
        )
        .first()
    )
    if existing:
        return existing

    now = datetime.now(timezone.utc)
    folder = StorageFolder(
        id=f"fld-{uuid.uuid4().hex[:16]}",
        name=name,
        parent_id=parent_id,
        owner_id=None,
        team_id=team_id,
        visibility="shared",
        path_cache="",
        created_by=user.user_id,
        created_at=now,
        updated_at=now,
        is_deleted=False,
    )
    session.add(folder)
    session.flush()
    return folder


def _copy_folder_tree_to_team(
    session: Session,
    user: CurrentUser,
    source_folder_id: str,
    target_parent_id: str,
    team_id: str,
) -> FolderResponse:
    source = fs.get_folder_or_404(session, source_folder_id)
    dest = _ensure_shared_subfolder(session, user, team_id, target_parent_id, source.name)

    for file_row in list_files_in_folder(session, source.id):
        _copy_file_to_team_folder(session, user, file_row, dest, team_id)

    children = (
        session.query(StorageFolder)
        .filter(StorageFolder.parent_id == source.id, StorageFolder.is_deleted.is_(False))
        .order_by(StorageFolder.name.asc())
        .all()
    )
    for child in children:
        _copy_folder_tree_to_team(session, user, child.id, dest.id, team_id)

    return fs._folder_to_response(dest)


def share_folder_to_team(session: Session, user: CurrentUser, req: ShareFolderRequest) -> FolderResponse:
    source = fs.get_folder_or_404(session, req.folder_id)
    if source.parent_id is None:
        raise HTTPException(status_code=400, detail="不能分享根目录")
    check_folder_access(session, user, source, write=True)
    check_team_access(session, user, req.team_id, write=True)

    if req.target_folder_id:
        target_folder = fs.get_folder_or_404(session, req.target_folder_id)
        if target_folder.visibility != "shared" or target_folder.team_id != req.team_id:
            raise HTTPException(status_code=400, detail="目标目录须为同一团队的共享目录")
    else:
        target_folder = fs.ensure_root_folder(session, user=user, visibility="shared", team_id=req.team_id)

    return _copy_folder_tree_to_team(session, user, source.id, target_folder.id, req.team_id)


def list_teams(session: Session, user: CurrentUser) -> list[TeamResponse]:
    if user.is_admin():
        teams = session.query(Team).filter(Team.is_deleted.is_(False)).order_by(Team.name.asc()).all()
        result: list[TeamResponse] = []
        for t in teams:
            count = (
                session.query(TeamMember)
                .filter(TeamMember.team_id == t.id, TeamMember.is_deleted.is_(False))
                .count()
            )
            result.append(
                TeamResponse(
                    id=t.id,
                    name=t.name,
                    description=t.description,
                    role="admin",
                    member_count=count,
                )
            )
        return result

    rows = (
        session.query(Team, TeamMember.role)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .filter(
            TeamMember.user_id == user.user_id,
            TeamMember.is_deleted.is_(False),
            Team.is_deleted.is_(False),
        )
        .order_by(Team.name.asc())
        .all()
    )
    out: list[TeamResponse] = []
    for team, role in rows:
        count = (
            session.query(TeamMember)
            .filter(TeamMember.team_id == team.id, TeamMember.is_deleted.is_(False))
            .count()
        )
        out.append(
            TeamResponse(
                id=team.id,
                name=team.name,
                description=team.description,
                role=role,
                member_count=count,
            )
        )
    return out


def create_team(session: Session, user: CurrentUser, req: TeamCreateRequest) -> TeamResponse:
    if not user.is_admin():
        raise HTTPException(status_code=403, detail="仅管理员可创建团队")
    now = datetime.now(timezone.utc)
    team_id = f"team-{uuid.uuid4().hex[:12]}"
    team = Team(
        id=team_id,
        name=req.name.strip(),
        description=req.description,
        created_by=user.user_id,
        created_at=now,
        updated_at=now,
        is_deleted=False,
    )
    session.add(team)
    session.add(
        TeamMember(
            id=f"tm-{uuid.uuid4().hex[:12]}",
            team_id=team_id,
            user_id=user.user_id,
            role="owner",
            created_at=now,
            is_deleted=False,
        )
    )
    session.flush()
    fs.ensure_root_folder(session, user=user, visibility="shared", team_id=team_id)
    return TeamResponse(id=team.id, name=team.name, description=team.description, role="owner", member_count=1)


def add_team_member(session: Session, user: CurrentUser, team_id: str, req: TeamMemberAddRequest) -> dict:
    if not user.is_admin():
        raise HTTPException(status_code=403, detail="仅管理员可添加团队成员")

    team = session.query(Team).filter(Team.id == team_id, Team.is_deleted.is_(False)).first()
    if not team:
        raise HTTPException(status_code=404, detail="团队不存在")

    existing = (
        session.query(TeamMember)
        .filter(
            TeamMember.team_id == team_id,
            TeamMember.user_id == req.user_id,
            TeamMember.is_deleted.is_(False),
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="用户已在团队中")

    session.add(
        TeamMember(
            id=f"tm-{uuid.uuid4().hex[:12]}",
            team_id=team_id,
            user_id=req.user_id,
            role=req.role,
            created_at=datetime.now(timezone.utc),
            is_deleted=False,
        )
    )
    return {"success": True, "team_id": team_id, "user_id": req.user_id}


def list_team_members(session: Session, user: CurrentUser, team_id: str) -> list[TeamMemberResponse]:
    if not user.is_admin():
        check_team_access(session, user, team_id, write=False)

    team = session.query(Team).filter(Team.id == team_id, Team.is_deleted.is_(False)).first()
    if not team:
        raise HTTPException(status_code=404, detail="团队不存在")

    rows = (
        session.query(TeamMember)
        .filter(TeamMember.team_id == team_id, TeamMember.is_deleted.is_(False))
        .order_by(TeamMember.created_at.asc())
        .all()
    )
    return [
        TeamMemberResponse(id=row.id, user_id=row.user_id, role=row.role, created_at=row.created_at)
        for row in rows
    ]


def remove_team_member(session: Session, user: CurrentUser, team_id: str, member_user_id: str) -> dict:
    if not user.is_admin():
        raise HTTPException(status_code=403, detail="仅管理员可移除团队成员")

    team = session.query(Team).filter(Team.id == team_id, Team.is_deleted.is_(False)).first()
    if not team:
        raise HTTPException(status_code=404, detail="团队不存在")

    member = (
        session.query(TeamMember)
        .filter(
            TeamMember.team_id == team_id,
            TeamMember.user_id == member_user_id,
            TeamMember.is_deleted.is_(False),
        )
        .first()
    )
    if not member:
        raise HTTPException(status_code=404, detail="成员不在团队中")

    member.is_deleted = True
    return {"success": True, "team_id": team_id, "user_id": member_user_id}


def update_team_member_role(
    session: Session, user: CurrentUser, team_id: str, member_user_id: str, role: str
) -> dict:
    if not user.is_admin():
        raise HTTPException(status_code=403, detail="仅管理员可修改成员权限")

    if role not in ("owner", "member", "viewer"):
        raise HTTPException(status_code=400, detail="无效的角色")

    member = (
        session.query(TeamMember)
        .filter(
            TeamMember.team_id == team_id,
            TeamMember.user_id == member_user_id,
            TeamMember.is_deleted.is_(False),
        )
        .first()
    )
    if not member:
        raise HTTPException(status_code=404, detail="成员不在团队中")

    member.role = role
    return {"success": True, "team_id": team_id, "user_id": member_user_id, "role": role}


def delete_team(session: Session, user: CurrentUser, team_id: str) -> dict:
    if not user.is_admin():
        raise HTTPException(status_code=403, detail="仅管理员可删除团队")

    team = session.query(Team).filter(Team.id == team_id, Team.is_deleted.is_(False)).first()
    if not team:
        raise HTTPException(status_code=404, detail="团队不存在")

    now = datetime.now(timezone.utc)
    team.is_deleted = True
    team.updated_at = now
    members = session.query(TeamMember).filter(TeamMember.team_id == team_id, TeamMember.is_deleted.is_(False)).all()
    for member in members:
        member.is_deleted = True

    return {"success": True, "team_id": team_id}
