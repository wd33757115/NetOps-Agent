# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Skill / Workflow 产物下载（签名 URL，经 BFF 代理 MinIO）。"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from src.infrastructure.storage.download_urls import verify_download_signature
from src.infrastructure.storage.minio_client import get_minio_storage

router = APIRouter(prefix="/api/v1/artifacts", tags=["Artifacts"])


@router.get("/download")
async def download_artifact(
    key: str = Query(..., description="MinIO object key"),
    exp: int = Query(..., description="过期 Unix 时间戳"),
    sig: str = Query(..., description="HMAC 签名"),
    filename: str | None = Query(None, description="下载文件名"),
):
    if key.startswith(('inspection/', 'reports/patrol/')):
        raise HTTPException(status_code=403, detail="请通过巡检应用授权下载产物")
    if key.startswith('apps/firewall-policy/'):
        raise HTTPException(status_code=403, detail="请通过防火墙应用授权下载产物")
    if not verify_download_signature(key, exp, sig):
        raise HTTPException(status_code=403, detail="无效或过期的下载链接")

    storage = get_minio_storage()
    if not storage.is_ready():
        raise HTTPException(status_code=503, detail="MinIO 未就绪")

    data = storage.download_file(key)
    if data is None:
        raise HTTPException(status_code=404, detail="文件不存在")

    name = (filename or key.rsplit("/", 1)[-1] or "download").strip()
    encoded_name = quote(name)
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}",
        },
    )
