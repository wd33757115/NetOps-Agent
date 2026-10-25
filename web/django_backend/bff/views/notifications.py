# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

import logging

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from ..decorators import require_jwt
from ..sync_async import sync_bff_view
from ..proxy_client import TASK_TIMEOUT, proxy_to_fastapi
from ._helpers import forward_client_headers

logger = logging.getLogger("bff.views.notifications")


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_notifications_list(request: HttpRequest) -> JsonResponse:
    query = request.GET.urlencode()
    path = "/api/v1/notifications/"
    if query:
        path = f"{path}?{query}"
    return await proxy_to_fastapi(
        method="GET",
        fastapi_path=path,
        request_id=request.bff_request_id,
        timeout=TASK_TIMEOUT,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_notifications_clear(request: HttpRequest) -> JsonResponse:
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path="/api/v1/notifications/clear",
        request_id=request.bff_request_id,
        timeout=TASK_TIMEOUT,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_notification_read(request: HttpRequest, notification_id: str) -> JsonResponse:
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path=f"/api/v1/notifications/{notification_id}/read",
        request_id=request.bff_request_id,
        timeout=TASK_TIMEOUT,
        extra_headers=forward_client_headers(request),
    )
