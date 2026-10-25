# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""网盘 API Pydantic 模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Visibility = Literal["private", "shared"]


class TeamCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str | None = None


class TeamMemberAddRequest(BaseModel):
    user_id: str
    role: str = Field(default="member", description="owner | member | viewer")


class TeamMemberUpdateRequest(BaseModel):
    role: str = Field(..., description="owner | member | viewer")


class FolderCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    parent_id: str | None = None
    visibility: Visibility = "private"
    team_id: str | None = None


class FolderResponse(BaseModel):
    id: str
    name: str
    parent_id: str | None
    visibility: str
    team_id: str | None
    owner_id: str | None
    created_at: datetime | None
    updated_at: datetime | None


class FolderTreeNode(BaseModel):
    id: str
    name: str
    parent_id: str | None
    children: list["FolderTreeNode"] = Field(default_factory=list)


class FileResponse(BaseModel):
    id: str
    name: str
    folder_id: str | None
    visibility: str
    team_id: str | None
    owner_id: str | None
    content_type: str | None
    size_bytes: int
    created_at: datetime | None
    updated_at: datetime | None


class StorageListResponse(BaseModel):
    folder: FolderResponse | None
    folders: list[FolderResponse]
    files: list[FileResponse]
    breadcrumb: list[FolderResponse]


class UploadInitRequest(BaseModel):
    filename: str = Field(..., min_length=1, max_length=512)
    folder_id: str | None = None
    visibility: Visibility = "private"
    team_id: str | None = None
    content_type: str = "application/octet-stream"
    size_bytes: int = Field(default=0, ge=0)


class UploadInitResponse(BaseModel):
    file_id: str
    object_key: str
    # A path on the workbench address (/object-storage/...); PUT the file there.
    upload_url: str
    expires_in: int


class UploadCompleteRequest(BaseModel):
    file_id: str
    size_bytes: int | None = None


class DownloadResponse(BaseModel):
    file_id: str
    filename: str
    download_url: str
    expires_in: int


class ShareFileRequest(BaseModel):
    file_id: str
    team_id: str
    target_folder_id: str | None = None


class ShareFolderRequest(BaseModel):
    folder_id: str
    team_id: str
    target_folder_id: str | None = None


class RenameRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=512)


class MoveRequest(BaseModel):
    target_folder_id: str


class CopyFileRequest(BaseModel):
    target_folder_id: str
    name: str | None = Field(default=None, max_length=512, description="可选新文件名，默认自动生成副本名")


class TeamMemberResponse(BaseModel):
    id: str
    user_id: str
    role: str
    created_at: datetime | None = None


class TeamResponse(BaseModel):
    id: str
    name: str
    description: str | None
    role: str | None = None
    member_count: int = 0
