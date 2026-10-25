# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""网盘路径解析与安全规范化（object_key / 目录链）。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy.orm import Session

from src.infrastructure.db.models import StorageFolder

ROOT_NAME = "根目录"
_RESERVED_SEGMENTS = frozenset({".", ".."})
_SAFE_SEGMENT = re.compile(r"[^a-zA-Z0-9_\-\u4e00-\u9fff.]+")


@dataclass(frozen=True)
class FolderNamespace:
    """目录树根命名空间（从根到当前节点校验后得出）。"""

    visibility: str
    owner_id: str | None
    team_id: str | None
    root_id: str


def sanitize_name_segment(name: str, *, field: str = "名称") -> str:
    """单段路径名消毒：禁止路径分隔符与 `..` 穿越。"""
    raw = (name or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail=f"{field}不能为空")

    normalized = raw.replace("\\", "/")
    if "/" in normalized:
        raise HTTPException(status_code=400, detail=f"{field}不能包含路径分隔符")

    segment = _SAFE_SEGMENT.sub("_", normalized).strip("._")
    if not segment or segment in _RESERVED_SEGMENTS:
        raise HTTPException(status_code=400, detail=f"{field}非法")
    return segment


def sanitize_filename(filename: str) -> str:
    """上传/存储文件名消毒（禁止路径分隔符与 `..` 穿越）。"""
    raw = (filename or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="文件名不能为空")

    normalized = raw.replace("\\", "/")
    parts = [p for p in normalized.split("/") if p]
    if not parts:
        raise HTTPException(status_code=400, detail="文件名非法")
    if len(parts) > 1:
        raise HTTPException(status_code=400, detail="文件名不能包含路径分隔符")
    if any(p in _RESERVED_SEGMENTS for p in parts):
        raise HTTPException(status_code=400, detail="文件名非法")

    return sanitize_name_segment(parts[0], field="文件名")


def _get_folder_row(session: Session, folder_id: str) -> StorageFolder:
    row = (
        session.query(StorageFolder)
        .filter(StorageFolder.id == folder_id, StorageFolder.is_deleted.is_(False))
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="目录不存在")
    return row


def resolve_folder_chain(session: Session, folder: StorageFolder) -> tuple[list[StorageFolder], FolderNamespace]:
    """自底向上解析目录链，并校验可见性/归属一致性。"""
    if folder.is_deleted:
        raise HTTPException(status_code=404, detail="目录不存在")

    chain: list[StorageFolder] = []
    current: StorageFolder | None = folder
    visited: set[str] = set()

    while current is not None:
        if current.id in visited:
            raise HTTPException(status_code=500, detail="目录树存在循环引用")
        visited.add(current.id)
        chain.append(current)
        if not current.parent_id:
            break
        current = _get_folder_row(session, current.parent_id)

    chain.reverse()
    root = chain[0]
    visibility = root.visibility
    owner_id = root.owner_id
    team_id = root.team_id

    if visibility == "private" and not owner_id:
        raise HTTPException(status_code=500, detail="个人根目录缺少 owner_id")
    if visibility == "shared" and not team_id:
        raise HTTPException(status_code=500, detail="团队根目录缺少 team_id")

    for node in chain:
        if node.visibility != visibility:
            raise HTTPException(status_code=500, detail="目录可见性与祖先不一致")
        if visibility == "private":
            if node.owner_id and node.owner_id != owner_id:
                raise HTTPException(status_code=500, detail="个人目录归属与祖先不一致")
        elif node.team_id and node.team_id != team_id:
            raise HTTPException(status_code=500, detail="团队目录归属与祖先不一致")

    namespace = FolderNamespace(
        visibility=visibility,
        owner_id=owner_id,
        team_id=team_id,
        root_id=root.id,
    )
    return chain, namespace


def build_relative_path(chain: list[StorageFolder]) -> str:
    parts: list[str] = []
    for node in chain:
        if node.name != ROOT_NAME:
            parts.append(sanitize_name_segment(node.name, field="文件夹名称"))
    return "/".join(parts)


def build_object_prefix(session: Session, folder: StorageFolder) -> str:
    """根据目录链根命名空间生成 MinIO 前缀（与当前操作用户无关）。"""
    chain, ns = resolve_folder_chain(session, folder)
    rel = build_relative_path(chain)
    if ns.visibility == "shared":
        base = f"shared/teams/{ns.team_id}"
    else:
        base = f"private/users/{ns.owner_id}"
    return f"{base}/{rel}".rstrip("/") if rel else base


def build_object_key(session: Session, folder: StorageFolder, filename: str) -> str:
    prefix = build_object_prefix(session, folder)
    safe_name = sanitize_filename(filename)
    return f"{prefix}/{safe_name}"


def assert_key_within_prefix(object_key: str, prefix: str) -> None:
    """防止 object_key 逃逸出预期前缀。"""
    if not object_key.startswith(prefix.rstrip("/") + "/") and object_key != prefix.rstrip("/"):
        raise HTTPException(status_code=500, detail="对象路径与目录命名空间不一致")
