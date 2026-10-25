# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""MinIO 网盘 REST API。"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from src.auth.dependencies import get_current_user, require_role
from src.auth.models import CurrentUser
from src.gateway.audit_service import write_audit_log
from src.infrastructure.db.postgres import get_db_session
from src.infrastructure.storage.minio_client import get_minio_storage
from src.storage import file_service, folder_service
from src.storage.schemas import (
    CopyFileRequest,
    FileResponse,
    FolderCreateRequest,
    FolderResponse,
    FolderTreeNode,
    MoveRequest,
    RenameRequest,
    ShareFileRequest,
    ShareFolderRequest,
    StorageListResponse,
    TeamCreateRequest,
    TeamMemberAddRequest,
    TeamMemberResponse,
    TeamMemberUpdateRequest,
    TeamResponse,
    UploadCompleteRequest,
    UploadInitRequest,
)

router = APIRouter(prefix="/api/v1/storage", tags=["Storage"])


def _audit(request: Request, user: CurrentUser, action: str, resource_type: str, resource_id: str, detail: dict | None = None):
    write_audit_log(
        action=action,
        user_id=user.user_id,
        username=user.username,
        resource_type=resource_type,
        resource_id=resource_id,
        detail=detail,
        ip_address=request.client.host if request.client else None,
    )


@router.get("/health")
async def storage_health():
    storage = get_minio_storage()
    return {"minio_ready": storage.is_ready(), "bucket": storage.bucket_name}


@router.get("/teams", response_model=list[TeamResponse])
async def list_teams(user: CurrentUser = Depends(get_current_user)):
    with get_db_session() as session:
        return file_service.list_teams(session, user)


@router.post("/teams", response_model=TeamResponse)
async def create_team(
    request: Request,
    body: TeamCreateRequest,
    user: CurrentUser = Depends(require_role(["admin"])),
):
    with get_db_session() as session:
        team = file_service.create_team(session, user, body)
        _audit(request, user, "storage_team_create", "team", team.id, {"name": team.name})
        return team


@router.post("/teams/{team_id}/members")
async def add_team_member(
    request: Request,
    team_id: str,
    body: TeamMemberAddRequest,
    user: CurrentUser = Depends(require_role(["admin"])),
):
    with get_db_session() as session:
        result = file_service.add_team_member(session, user, team_id, body)
        _audit(request, user, "storage_team_add_member", "team", team_id, body.model_dump())
        return result


@router.get("/teams/{team_id}/members", response_model=list[TeamMemberResponse])
async def list_team_members(team_id: str, user: CurrentUser = Depends(get_current_user)):
    with get_db_session() as session:
        return file_service.list_team_members(session, user, team_id)


@router.patch("/teams/{team_id}/members/{member_user_id}")
async def update_team_member(
    request: Request,
    team_id: str,
    member_user_id: str,
    body: TeamMemberUpdateRequest,
    user: CurrentUser = Depends(require_role(["admin"])),
):
    with get_db_session() as session:
        result = file_service.update_team_member_role(session, user, team_id, member_user_id, body.role)
        _audit(request, user, "storage_team_update_member", "team", team_id, {"user_id": member_user_id, "role": body.role})
        return result


@router.delete("/teams/{team_id}/members/{member_user_id}")
async def remove_team_member(
    request: Request,
    team_id: str,
    member_user_id: str,
    user: CurrentUser = Depends(require_role(["admin"])),
):
    with get_db_session() as session:
        result = file_service.remove_team_member(session, user, team_id, member_user_id)
        _audit(request, user, "storage_team_remove_member", "team", team_id, {"user_id": member_user_id})
        return result


@router.delete("/teams/{team_id}")
async def delete_team(
    request: Request,
    team_id: str,
    user: CurrentUser = Depends(require_role(["admin"])),
):
    with get_db_session() as session:
        result = file_service.delete_team(session, user, team_id)
        _audit(request, user, "storage_team_delete", "team", team_id)
        return result


@router.post("/folders", response_model=FolderResponse)
async def create_folder(
    request: Request,
    body: FolderCreateRequest,
    user: CurrentUser = Depends(get_current_user),
):
    with get_db_session() as session:
        folder = folder_service.create_folder(session, user, body)
        _audit(request, user, "storage_folder_create", "folder", folder.id, body.model_dump())
        return folder


@router.delete("/folders/{folder_id}")
async def delete_folder(
    request: Request,
    folder_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    with get_db_session() as session:
        folder_service.delete_folder(session, user, folder_id)
        _audit(request, user, "storage_folder_delete", "folder", folder_id)
        return {"success": True}


@router.patch("/folders/{folder_id}", response_model=FolderResponse)
async def rename_folder(
    request: Request,
    folder_id: str,
    body: RenameRequest,
    user: CurrentUser = Depends(get_current_user),
):
    with get_db_session() as session:
        folder = folder_service.rename_folder(session, user, folder_id, body.name)
        _audit(request, user, "storage_folder_rename", "folder", folder_id, {"name": body.name})
        return folder


@router.post("/folders/{folder_id}/move", response_model=FolderResponse)
async def move_folder(
    request: Request,
    folder_id: str,
    body: MoveRequest,
    user: CurrentUser = Depends(get_current_user),
):
    with get_db_session() as session:
        folder = folder_service.move_folder(session, user, folder_id, body.target_folder_id)
        _audit(request, user, "storage_folder_move", "folder", folder_id, body.model_dump())
        return folder


@router.get("/folders/tree", response_model=FolderTreeNode)
async def folder_tree(
    visibility: str = Query("private"),
    team_id: str | None = Query(None),
    user: CurrentUser = Depends(get_current_user),
):
    with get_db_session() as session:
        root = folder_service.ensure_root_folder(session, user=user, visibility=visibility, team_id=team_id)
        return folder_service.build_folder_tree(session, user, root)


@router.get("/list", response_model=StorageListResponse)
async def list_storage(
    folder_id: str | None = Query(None),
    visibility: str = Query("private"),
    team_id: str | None = Query(None),
    user: CurrentUser = Depends(get_current_user),
):
    with get_db_session() as session:
        current, child_folders = folder_service.list_children(
            session, user, folder_id=folder_id, visibility=visibility, team_id=team_id
        )
        files = file_service.list_files_in_folder(session, current.id)
        return StorageListResponse(
            folder=folder_service._folder_to_response(current),
            folders=[folder_service._folder_to_response(f) for f in child_folders],
            files=[file_service._file_to_response(f) for f in files],
            breadcrumb=folder_service.build_breadcrumb(session, current),
        )


@router.post("/upload/init")
async def upload_init(
    request: Request,
    body: UploadInitRequest,
    user: CurrentUser = Depends(get_current_user),
):
    with get_db_session() as session:
        result = file_service.init_upload(session, user, body)
        _audit(request, user, "storage_upload_init", "file", result.file_id, {"name": body.filename})
        return result


@router.post("/upload/complete")
async def upload_complete(
    request: Request,
    body: UploadCompleteRequest,
    user: CurrentUser = Depends(get_current_user),
):
    with get_db_session() as session:
        file_row = file_service.complete_upload(session, user, body)
        _audit(request, user, "storage_upload_complete", "file", file_row.id)
        return file_row


@router.get("/files/{file_id}/download")
async def download_file(
    request: Request,
    file_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    with get_db_session() as session:
        result = file_service.get_download_url(session, user, file_id)
        _audit(request, user, "storage_download", "file", file_id)
        return result


@router.get("/files/{file_id}/content")
async def file_content(
    request: Request,
    file_id: str,
    disposition: str = Query("inline", description="inline 或 attachment"),
    user: CurrentUser = Depends(get_current_user),
):
    disp = "attachment" if disposition == "attachment" else "inline"
    with get_db_session() as session:
        filename, content_type, data = file_service.read_file_content(session, user, file_id)
        _audit(request, user, "storage_file_content", "file", file_id, {"disposition": disp})
        encoded_name = quote(filename)
        return Response(
            content=data,
            media_type=content_type,
            headers={
                "Content-Disposition": f"{disp}; filename*=UTF-8''{encoded_name}",
            },
        )


@router.delete("/files/{file_id}")
async def delete_file(
    request: Request,
    file_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    with get_db_session() as session:
        file_service.delete_file(session, user, file_id)
        _audit(request, user, "storage_file_delete", "file", file_id)
        return {"success": True}


@router.patch("/files/{file_id}")
async def rename_file(
    request: Request,
    file_id: str,
    body: RenameRequest,
    user: CurrentUser = Depends(get_current_user),
):
    with get_db_session() as session:
        file_row = file_service.rename_file(session, user, file_id, body)
        _audit(request, user, "storage_file_rename", "file", file_id, {"name": body.name})
        return file_row


@router.post("/files/{file_id}/move")
async def move_file(
    request: Request,
    file_id: str,
    body: MoveRequest,
    user: CurrentUser = Depends(get_current_user),
):
    with get_db_session() as session:
        file_row = file_service.move_file(session, user, file_id, body)
        _audit(request, user, "storage_file_move", "file", file_id, body.model_dump())
        return file_row


@router.post("/files/{file_id}/copy", response_model=FileResponse)
async def copy_file(
    request: Request,
    file_id: str,
    body: CopyFileRequest,
    user: CurrentUser = Depends(get_current_user),
):
    with get_db_session() as session:
        file_row = file_service.copy_file(session, user, file_id, body)
        _audit(request, user, "storage_file_copy", "file", file_id, body.model_dump())
        return file_row


@router.post("/share")
async def share_file(
    request: Request,
    body: ShareFileRequest,
    user: CurrentUser = Depends(get_current_user),
):
    with get_db_session() as session:
        file_row = file_service.share_file_to_team(session, user, body)
        _audit(request, user, "storage_share", "file", body.file_id, {"team_id": body.team_id})
        return file_row


@router.post("/share/folder", response_model=FolderResponse)
async def share_folder(
    request: Request,
    body: ShareFolderRequest,
    user: CurrentUser = Depends(get_current_user),
):
    with get_db_session() as session:
        folder = file_service.share_folder_to_team(session, user, body)
        _audit(request, user, "storage_share_folder", "folder", body.folder_id, {"team_id": body.team_id})
        return folder
