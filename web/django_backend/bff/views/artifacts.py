# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

import logging

from django.http import HttpRequest
from django.views.decorators.csrf import csrf_exempt

from ..proxy_client import proxy_binary_to_fastapi
from ..sync_async import sync_bff_view

logger = logging.getLogger("bff.views.artifacts")


@csrf_exempt
@sync_bff_view
async def proxy_artifact_download(request: HttpRequest):
    """Skill/Workflow 产物下载（HMAC 签名，无需 JWT）。"""
    qs = request.META.get("QUERY_STRING", "")
    path = "/api/v1/artifacts/download"
    if qs:
        path = f"{path}?{qs}"
    return await proxy_binary_to_fastapi(
        method="GET",
        fastapi_path=path,
        request_id=getattr(request, "bff_request_id", "artifact-download"),
        timeout=120,
    )
